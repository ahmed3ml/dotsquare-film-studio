"""Autonomous production controller ("film" commands).

Design
- The workflow engine (PROJECT_STATUS.json via studio.py commands) is the ONLY authority on stage/gate/review/shot
  state. The orchestrator never keeps its own idea of "where the film is": every step re-reads the engine and derives
  the next action (plan()), so it survives restarts and never depends on conversational memory.
- Its own files (projects/<ID>/orchestrator/: state.json, journal.jsonl, jobs/, run.lock, PAUSE, runner.log) only
  record jobs (who ran what, cost, outcome), waits/blocks and render manifests. Agents cannot write there (hook).
- Every engine mutation goes through studio.py commands (same checks as for a human), including policy approvals
  (`approve --policy`), which the engine refuses unless the project's approval policy allows it.
- Jobs are idempotent and keyed deterministically (e.g. review:RC-0004, revise:RC-0003); a single run lock prevents
  duplicate runners; RUNNING jobs found after a crash are re-run; checkpoints are written after every step.
"""
import contextlib
import datetime
import fcntl
import hashlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import assembly  # noqa: E402
import autonomy as A  # noqa: E402
import backends as B  # noqa: E402
import providers as P  # noqa: E402

C = None   # core module (studio.py)
X = None   # ext module
ORCH = "orchestrator"
ENGINE_LOCK = threading.RLock()
ACCEPTED = ("ACCEPTED", "ACCEPTED_BY_HUMAN", "ACCEPTED_BY_POLICY")
REVIEW_TOOLS = ["Bash(studio/bin/media-crop *)", "Bash(studio/bin/media-frames *)"]
ROUGH = "media/rough_cut/ROUGH_CUT.mp4"
FINAL = "media/final/FINAL.mp4"
STAGE_AR = {"research": "البحث التاريخي", "story": "المعالجة القصصية", "screenplay": "كتابة السيناريو",
            "script_review": "مراجعة السيناريو", "directing": "خطة الإخراج", "visual_development": "تصميم الشخصيات والبيئات",
            "storyboard": "اللوحة القصصية وقائمة اللقطات", "proof_of_concept": "فيديو إثبات المفهوم",
            "production_planning": "الميزانية وخطة التنفيذ", "image_production": "توليد الصور المرجعية",
            "video_production": "توليد الفيديو", "sound": "الصوت والحوار والموسيقى", "editing": "المونتاج (النسخة المبدئية)",
            "quality_control": "الفحص النهائي والإخراج النهائي", "delivery": "التسليم"}


def now():
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def sha(path):
    return C.sha256_file(path)


class Stop(Exception):
    pass


# ------------------------------------------------------------------ engine bridge
def engine(pdir, *argv):
    """Run a studio.py command in-process (serialised). Returns (exit_code, output)."""
    with ENGINE_LOCK:
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                code = C.main(["--projects-dir", str(Path(pdir).parent)] + [str(a) for a in argv] + ["-p", Path(pdir).name])
        except SystemExit as e:  # argparse errors
            code = int(e.code or 2)
        return code, buf.getvalue()


def pipeline():
    return json.loads((C.STUDIO_ROOT / "standards" / "pipeline.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ controller
class Orchestrator:
    def __init__(self, pdir, backend=None, parallel=None):
        self.pdir = Path(pdir)
        self.pid = self.pdir.name
        self.odir = self.pdir / ORCH
        self.odir.mkdir(exist_ok=True)
        self.state = self._load_state()
        self.pipe = pipeline()
        self.crit = X.criteria()["sets"]
        self.backend = backend or self._make_backend()
        self.parallel = int(parallel or self.state.get("parallel") or 1)
        self.stop_flag = False
        self._lockf = None

    # ---- persistence
    def _load_state(self):
        p = self.odir / "state.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        return {"project_id": self.pdir.name, "status": "IDLE", "backend": "claude", "mock_plan": {}, "jobs": {},
                "blocked": {}, "loop_baseline": {}, "reports": {}, "renders": {}, "inspections": {}, "item_wait": {},
                "llm_cost_usd": 0.0, "created_at": now(), "intake": None, "delivered": None, "retry_at": None,
                "waiting": [], "parallel": 1}

    def save(self):
        with ENGINE_LOCK:
            self.state["updated_at"] = now()
            tmp = self.odir / "state.json.tmp"
            tmp.write_text(json.dumps(self.state, indent=1, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.odir / "state.json")

    def journal(self, event, **kw):
        rec = {"ts": now(), "event": event}
        rec.update(kw)
        with ENGINE_LOCK, open(self.odir / "journal.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _make_backend(self):
        pol = A.load_policy(self.pdir)
        if self.state.get("backend") == "mock":
            return B.MockBackend(self.state.get("mock_plan"))
        return B.ClaudeBackend(per_job_cap_usd=pol.get("spending", {}).get("per_job_llm_cap_usd", 15))

    # ---- locking / recovery
    def acquire(self):
        self._lockf = open(self.odir / "run.lock", "a+")
        try:
            fcntl.flock(self._lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lockf.close()
            self._lockf = None
            raise C.StudioError("ALREADY_RUNNING: another runner holds %s/run.lock (pid %s)" % (
                ORCH, (self.odir / "run.pid").read_text().strip() if (self.odir / "run.pid").exists() else "?"))
        (self.odir / "run.pid").write_text(str(os.getpid()))

    def release(self):
        if self._lockf:
            try:
                (self.odir / "run.pid").unlink()
            except OSError:
                pass
            fcntl.flock(self._lockf, fcntl.LOCK_UN)
            self._lockf.close()
            self._lockf = None

    def recover(self):
        n = 0
        for key, j in self.state["jobs"].items():
            if j["status"] == "RUNNING":
                j["status"] = "INTERRUPTED"
                n += 1
                self.journal("JOB_INTERRUPTED_RECOVERED", job=key)
        if n:
            self.save()
        return n

    # ---- helpers on engine state
    def st(self):
        return C.load(self.pdir)

    def policy(self):
        return A.load_policy(self.pdir)

    def doc_ready(self, rel):
        p = self.pdir / rel
        if not p.exists():
            return False
        if rel.endswith((".md", ".csv", ".json", ".txt")):
            t = p.read_text(encoding="utf-8")
            if C.PLACEHOLDER in t or not t.strip():
                return False
            if rel.endswith(".csv") and len(C.read_csv(p)) == 0:
                return False
        if rel == "SHOT_LIST.csv":
            for r in C.read_csv(p):
                for need in (P.default_prompt("image-generation", r["shot_id"]), P.default_prompt("video-generation", r["shot_id"])):
                    if not (self.pdir / need).exists():
                        return False
        return True

    def digest(self, rels):
        h = hashlib.sha256()
        for r in rels:
            p = self.pdir / r
            h.update((r + ":" + (sha(p) if p.exists() else "missing")).encode())
        return h.hexdigest()

    def failed_streak(self, st, target, cset):
        base = self.state["loop_baseline"].get(target)
        cycles = [r for r in st["reviews"] if r["target"] == target and r["set"] == cset]
        if base:
            ids = [r["id"] for r in cycles]
            cycles = cycles[ids.index(base) + 1:] if base in ids else cycles
        n = 0
        for r in reversed(cycles):
            if r["status"] == "VOID":
                continue
            if r["status"] == "REVISION_NEEDED":
                n += 1
            else:
                break
        return n

    def job_needed(self, key):
        j = self.state["jobs"].get(key)
        lim = self.policy().get("review_loop", {}).get("max_job_retries", 2) + 1
        if j is None or j["status"] in ("PENDING", "INTERRUPTED"):
            return True
        if j["status"] == "TRANSIENT":
            return True
        if j["status"] == "FAILED":
            return j["attempts"] < lim
        return False

    def job_done(self, key):
        j = self.state["jobs"].get(key)
        return bool(j and j["status"] == "DONE")

    # ------------------------------------------------------------------ planning (pure: reads only)
    def plan(self):
        st = self.st()
        acts, waits = [], []
        if self.state.get("intake") and not self.job_done("intake"):
            if self.job_needed("intake"):
                acts.append(self.job("intake", "intake", "executive-producer", "research", "project", files=["PROJECT.md"],
                                     extra={"brief": self.state["intake"]["brief"]}))
            return acts, waits
        for name in C.STAGE_NAMES:
            s = st["stages"][name]
            if s["state"] == "DONE":
                continue
            if s["state"].startswith("BLOCKED"):
                waits.append({"type": "blocked", "stage": name, "reason": s.get("note") or s["state"]})
                continue
            if s["state"] == "NOT_STARTED":
                try:
                    C.stage_check_startable(st, name)
                except C.StudioError:
                    continue
                acts.append(("engine", ("stage", "start", name), name))
                continue
            spec = self.pipe["stages"][name]
            kind = spec["kind"]
            if kind == "document":
                self.plan_document(st, name, spec, acts, waits)
            elif kind == "media":
                self.plan_media(st, name, spec, acts, waits)
            elif kind == "proof":
                self.plan_proof(st, name, spec, acts, waits)
            elif kind == "edit":
                self.plan_edit(st, name, spec, acts, waits)
            elif kind == "final_render":
                self.plan_final(st, name, spec, acts, waits)
        return acts, waits

    def stage_target(self, name):
        if name not in C.STAGE_BY:
            return "stage:" + str(name)
        g = C.STAGE_BY[name][3]
        return ("gate:" + g) if g else ("stage:" + name)

    def plan_estimate(self, st, name, acts, waits):
        """GATE-07 needs a real cost estimate. Prices come only from providers.json pricing (never guessed)."""
        rows = [r for r in C.read_csv(self.pdir / "COST_TRACKER.csv") if r["kind"] == "ESTIMATE" and r["stage"] == name]
        if rows:
            return True
        cfg = P.load_config(self.pdir)
        r = st["budget"]["rates"]
        retry = 1 + float(r.get("retry_allowance") or 0.25)
        shots = self.shots()
        secs = sum(float(x.get("duration_s") or 0) for x in shots)
        lines = [("image-generation", "keyframes (incl. retries)", len(shots) * int(r.get("images_per_shot") or 1) * retry),
                 (self.video_cap(cfg), "video seconds (incl. retries)", secs * int(r.get("video_attempts_per_shot") or 1) * retry),
                 ("speech-generation", "dialogue seconds (incl. retries)",
                  sum(float(x.get("duration_s") or 0) for x in shots
                      if (self.pdir / P.default_prompt("speech-generation", x["shot_id"])).exists()) * retry)]
        for bed, cap in self.pipe["beds"].items():
            if P.routed_type(cfg, cap) not in (None, "prompt-file"):
                lines.append((cap, "%s bed seconds" % bed.lower(), secs))
        unknown = []
        for cap, item, units in lines:
            if units <= 0:
                continue
            pname, prov, _ = P.resolve(cfg, cap)
            price = P.unit_price(prov, cap)
            if prov["type"] == "prompt-file" or price is None:
                unknown.append("%s (%s)" % (cap, pname))
        if unknown:
            waits.append({"type": "integration", "stage": name, "capability": ", ".join(unknown),
                          "reason": "PRICE_UNKNOWN: no connected provider with a real price for %s" % ", ".join(unknown)})
            return False
        acts.append(("internal", ("estimate", name, [(cap, item, round(u, 2)) for cap, item, u in lines if u > 0]), name))
        return False

    def job(self, key, kind, agent, stage, target, files=None, **kw):
        d = {"key": key, "kind": kind, "agent": agent, "stage": stage, "target": target, "files": files or []}
        d.update(kw)
        return ("job", d)

    # ---- documents and gates
    def plan_document(self, st, name, spec, acts, waits, files=None, reports_about=None, revise_files=None):
        gate = C.STAGE_BY[name][3]
        target = ("gate:" + gate) if gate else ("stage:" + name)
        if target in self.state["blocked"]:
            waits.append({"type": "blocked", "stage": name, "reason": self.state["blocked"][target]["reason"]})
            return
        files = files if files is not None else spec.get("files", [])
        missing = [f for f in files if not self.doc_ready(f)]
        if missing:
            key = "create:%s" % name
            if self.job_needed(key):
                extra = {}
                if name == "storyboard":
                    extra["holds"] = A.active_holds(self.pdir)
                acts.append(self.job(key, "create", spec["creator"], name, target, files=files, extra=extra))
            elif not self.job_done(key) and self.state["jobs"].get(key, {}).get("status") != "RUNNING":
                waits.append({"type": "blocked", "stage": name, "reason": "creation job failed repeatedly: %s" % key})
            return
        if not gate:
            acts.append(("engine", ("stage", "done", name), name))
            return
        if spec.get("cost_estimate") and not self.plan_estimate(st, name, acts, waits):
            return
        g = st["gates"][gate]
        if g["state"] == "APPROVED":
            return
        pol = self.policy()
        if g["state"] == "PENDING_APPROVAL":
            mode = A.gate_mode(pol, gate)
            probs = A.approval_problems(self.pdir, st, gate, by_policy=True) if mode == "policy" else []
            if mode == "policy" and pol.get("auto_approval", {}).get("enabled") and not probs:
                acts.append(("engine", ("approve", gate, "--policy", "--note", "orchestrator auto-approval"), name))
            else:
                ext = A.external_pending(pol, gate)
                waits.append({"type": "gate_approval", "gate": gate, "stage": name, "external": [r["id"] for r in ext],
                              "policy_problems": probs if mode == "policy" else [], "mode": mode})
            return
        for rep in spec.get("reports", []):
            about = reports_about if reports_about is not None else files
            dg = self.digest(about)
            if not (self.pdir / rep["file"]).exists() or self.state["reports"].get(rep["file"]) != dg or not self.doc_ready(rep["file"]):
                key = "report:%s:%s" % (rep["file"], dg[:12])
                if self.job_needed(key):
                    acts.append(self.job(key, "report", rep["agent"], name, target, files=[rep["file"]],
                                         extra={"report": rep["file"], "about": about, "digest": dg}))
                elif not self.job_done(key) and self.state["jobs"].get(key, {}).get("status") != "RUNNING":
                    waits.append({"type": "blocked", "stage": name, "reason": "report job failed: %s" % key})
                return
        cset = spec["review_set"]
        cyc = X.latest_cycle(st, target, cset)
        if g["state"] in ("REVISION_REQUESTED", "REJECTED") and cyc and cyc["status"] in ACCEPTED and X.files_unchanged(self.pdir, cyc["files"]):
            note = next((h.get("note") for h in reversed(g["history"]) if h["event"] in ("REVISION_REQUESTED", "REJECTED")), "")
            key = "revise:human:%s:%d" % (gate, len(g["history"]))
            if self.job_needed(key):
                acts.append(self.job(key, "revise", spec.get("revision_owner", spec["creator"]), name, target,
                                     files=revise_files or files, cycle_id="human request",
                                     findings=[{"id": "HUMAN", "severity": "MAJOR", "issue": note, "required_fix": note}]))
            return
        if cyc is None or cyc["status"] == "VOID":
            acts.append(("engine", ("review", "open", "--target", target, "--set", cset, "--creator", spec["creator"]), name))
            return
        if cyc["status"] == "OPEN":
            key = "review:%s" % cyc["id"]
            if self.job_needed(key):
                acts.append(self.review_job(key, cyc, name, target, cset))
            elif not self.job_done(key) and self.state["jobs"].get(key, {}).get("status") != "RUNNING":
                waits.append({"type": "blocked", "stage": name, "reason": "review job failed: %s" % key})
            return
        if cyc["status"] in ACCEPTED:
            if X.files_unchanged(self.pdir, cyc["files"]):
                acts.append(("engine", ("submit", gate, "--by", "orchestrator", "--note", "review %s accepted" % cyc["id"]), name))
            else:
                acts.append(("engine", ("review", "open", "--target", target, "--set", cset, "--creator", spec["creator"]), name))
            return
        if cyc["status"] == "NEEDS_HUMAN_REVIEW":
            waits.append({"type": "human_ack", "cycle": cyc["id"], "stage": name, "target": target})
            return
        # REVISION_NEEDED
        if not X.files_unchanged(self.pdir, cyc["files"]):
            acts.append(("engine", ("review", "open", "--target", target, "--set", cset, "--creator", spec["creator"]), name))
            return
        self.plan_repair(st, name, spec, target, cset, cyc, acts, waits, revise_files or files)

    def plan_repair(self, st, name, spec, target, cset, cyc, acts, waits, files, prompt_job=None):
        lim = self.policy().get("review_loop", {}).get("max_failed_cycles", 3)
        streak = self.failed_streak(st, target, cset)
        if streak >= lim:
            key = "diagnose:%s:%s" % (target, cyc["id"])
            rep = "reviews/diagnosis/%s_%s.md" % (target.replace(":", "_"), cyc["id"])
            if self.job_needed(key):
                acts.append(self.job(key, "diagnose", "executive-producer", name, target, files=[rep],
                                     extra={"failed": streak, "report": rep}))
            else:
                acts.append(("internal", ("block", target, name, "REVIEW_LOOP_LIMIT: %d failed independent reviews (last %s); "
                                          "diagnosis in %s" % (streak, cyc["id"], rep)), name))
            return
        if prompt_job:
            acts.append(prompt_job)
            return
        key = "revise:%s" % cyc["id"]
        if self.job_needed(key):
            acts.append(self.job(key, "revise", spec.get("revision_owner", spec["creator"]), name, target, files=files,
                                 cycle_id=cyc["id"], findings=cyc.get("findings", []) or [
                                     {"id": cid, "severity": "MAJOR", "issue": r.get("evidence"), "required_fix": "fix " + cid}
                                     for cid, r in cyc.get("results", {}).items() if r.get("result") == "FAIL"]))
        elif not self.job_done(key) and self.state["jobs"].get(key, {}).get("status") != "RUNNING":
            waits.append({"type": "blocked", "stage": name, "reason": "revision job failed: %s" % key})

    def review_job(self, key, cyc, stage, target, cset, inspections=None):
        reviewer = self.pipe["reviewers"][cset]
        if reviewer == cyc["creator"]:
            reviewer = "continuity-supervisor" if reviewer != "continuity-supervisor" else "film-quality-reviewer"
        return self.job(key, "review", reviewer, stage, target, files=list(cyc["files"]), cycle_id=cyc["id"], set=cset,
                        extra={"verdict": "reviews/verdicts/%s.json" % cyc["id"],
                               "evidence": "reviews/evidence/%s_review.md" % cyc["id"],
                               "inspections": inspections or [], "allow_tools": list(REVIEW_TOOLS)})

    # ---- media items
    def shots(self):
        p = self.pdir / "SHOT_LIST.csv"
        rows = C.read_csv(p) if p.exists() else []
        aside = self.state.get("set_aside", {})
        rows = [r for r in rows if r["shot_id"] not in aside]
        return sorted(rows, key=lambda r: float(r.get("seq") or 0))

    def video_cap(self, cfg):
        for cap in self.pipe["video_capability_preference"]:
            if P.routed_type(cfg, cap) not in (None, "prompt-file"):
                return cap
        return "video-generation"

    def items(self, which, phase, shots=None):
        cfg = P.load_config(self.pdir)
        out = []
        rows = shots if shots is not None else self.shots()
        if which == "keyframes":
            for r in rows:
                out.append({"shot": r["shot_id"], "cap": "image-generation", "target": "keyframe:" + r["shot_id"],
                            "set": "shot_media", "units": 1, "phase": phase, "shot_type": r.get("shot_type")})
        elif which == "shots":
            cap = self.video_cap(cfg)
            for r in rows:
                out.append({"shot": r["shot_id"], "cap": cap, "target": "shot:" + r["shot_id"], "set": "shot_media",
                            "units": float(r.get("duration_s") or 3), "phase": phase, "shot_type": r.get("shot_type"),
                            "needs_keyframe": P.needs_input(cap) or cap == "character-video"})
        elif which == "audio":
            total = sum(float(r.get("duration_s") or 0) for r in rows) or 10
            for r in rows:
                if (self.pdir / P.default_prompt("speech-generation", r["shot_id"])).exists():
                    out.append({"shot": r["shot_id"], "cap": "speech-generation", "target": "audio:" + r["shot_id"],
                                "set": "dialogue_audio", "units": float(r.get("duration_s") or 3), "phase": phase})
            for bed, cap in self.pipe["beds"].items():
                if P.routed_type(cfg, cap) not in (None, "prompt-file") and (self.pdir / P.default_prompt(cap, bed)).exists():
                    out.append({"shot": bed, "cap": cap, "target": "audio:" + bed, "set": "audio_bed", "units": total,
                                "phase": phase, "bed": True})
        return out

    def latest_output(self, shot, cap):
        rows = [r for r in X.gen_rows(self.pdir) if r["shot_id"] == shot and r["capability"] == cap]
        for r in reversed(rows):
            if r["outcome"] == "SUCCESS" and r["output_path"] and (self.pdir / r["output_path"]).exists():
                return r["output_path"]
        return None

    def approved_output(self, st, target, cset):
        cyc = X.latest_cycle(st, target, cset)
        if cyc and cyc["status"] in ACCEPTED and X.files_unchanged(self.pdir, cyc["files"]):
            return list(cyc["files"])[0]
        return None

    def wait_key(self):
        st = self.st()
        pol = self.policy()
        provs = self.pdir / "providers.json"
        return hashlib.sha256(json.dumps([len(st["authorizations"]), st["gates"]["GATE-07"]["state"],
                                          pol.get("spending"), [h.get("status") for h in pol.get("content_holds", [])],
                                          sha(provs) if provs.exists() else ""], sort_keys=True).encode()).hexdigest()[:16]

    def plan_items(self, st, name, spec, items, acts, waits):
        """Returns True when every item is approved."""
        cfg = P.load_config(self.pdir)
        all_ok = True
        wk = self.wait_key()
        for it in items:
            T = it["target"]
            if T in self.state["blocked"]:
                waits.append({"type": "blocked", "stage": name, "target": T, "reason": self.state["blocked"][T]["reason"]})
                all_ok = False
                continue
            if not it.get("bed"):
                hs = A.shot_holds(self.pdir, it["shot"])
                if hs:
                    waits.append({"type": "hold", "hold": hs[0]["id"], "target": T, "stage": name})
                    all_ok = False
                    continue
            if P.routed_type(cfg, it["cap"]) in (None, "prompt-file"):
                waits.append({"type": "integration", "capability": it["cap"], "target": T, "stage": name})
                all_ok = False
                continue
            iw = self.state["item_wait"].get(T)
            if iw and iw.get("key") == wk:
                waits.append({"type": iw["type"], "target": T, "stage": name, "reason": iw["reason"]})
                all_ok = False
                continue
            ok = self.plan_item(st, name, spec, it, acts, waits)
            all_ok = all_ok and ok
        return all_ok

    def plan_item(self, st, name, spec, it, acts, waits):
        T, cset, cap = it["target"], it["set"], it["cap"]
        prompt = P.default_prompt(cap, it["shot"])
        if not (self.pdir / prompt).exists():
            waits.append({"type": "blocked", "stage": name, "target": T, "reason": "PROMPT_MISSING: %s" % prompt})
            return False
        inp = None
        if it.get("needs_keyframe"):
            inp = self.approved_output(st, "keyframe:" + it["shot"], "shot_media")
            if not inp:
                return False  # waits for the keyframe item (dependency, not a block)
        out = self.latest_output(it["shot"], cap)
        cyc = X.latest_cycle(st, T, cset)
        if cyc and cyc["status"] == "ACCEPTED_BY_POLICY" and X.files_unchanged(self.pdir, cyc["files"]):
            out = list(cyc["files"])[0]
        if out and cyc and out in cyc["files"] and cyc["status"] in ("REVISION_NEEDED", "NEEDS_HUMAN_REVIEW"):
            r = self.plan_pick(st, name, T, cset, cyc, acts, waits)
            if r == "final":
                pkey = "prompt_revise:final:%s" % cyc["id"]
                if not self.job_done(pkey):
                    if self.job_needed(pkey):
                        acts.append(self.job(pkey, "prompt_revise", self.creator_for(name, cap), name, T, files=[prompt],
                                             findings=[{"id": "PICK", "severity": "BLOCKER",
                                                        "issue": self.state["picks"][cyc["id"]].get("reason", "")}] + cyc.get("findings", []),
                                             extra={"reason": "FINAL ATTEMPT: the best of two versions still breaks a hard rule. Rewrite the "
                                                    "prompt with a strategy that makes the violation impossible: every principal (CH-001..CH-010) "
                                                    "seen from behind, rear 3/4 or as a backlit silhouette with the face away from camera; "
                                                    "riders' feet hanging free with no stirrups or foot loops; follow PRODUCER_DIRECTIVES.md "
                                                    "(background men may show faces). Simplify the composition if needed."}))
                    return False
                n = self.state.setdefault("final_attempt", {}).get(T, {}).get("n", 0)
                if n >= 3:
                    self.state["picks"][cyc["id"]]["final_failed"] = True
                    sid = T.split(":", 1)[1]
                    self.state.setdefault("set_aside", {})[sid] = {"target": T, "reason": "final attempt generation failed 3 times", "at": now()}
                    self.journal("SET_ASIDE", target=T, reason="final attempt generation failed 3 times")
                    return False
                acts.append(("generate", dict(it, input=inp, revision=True, final_of=cyc["id"],
                                              strategy="final attempt after escalated pick %s" % cyc["id"]), name))
                return False
            if r:
                return False
        if out and cyc and out in cyc["files"]:
            if cyc["status"] == "OPEN":
                key = "review:%s" % cyc["id"]
                if self.job_needed(key):
                    acts.append(self.review_job(key, cyc, name, T, cset, inspections=self.state["inspections"].get(out, [])))
                return False
            if cyc["status"] in ACCEPTED and X.files_unchanged(self.pdir, cyc["files"]):
                if T.startswith("shot:"):
                    row = next((r for r in self.shots() if r["shot_id"] == it["shot"]), {})
                    if row.get("status") != "APPROVED":
                        acts.append(("engine", ("shot", "set-status", "--id", it["shot"], "--status", "APPROVED"), name))
                        return False
                return True
            if cyc["status"] == "NEEDS_HUMAN_REVIEW":
                waits.append({"type": "human_ack", "cycle": cyc["id"], "stage": name, "target": T})
                return False
            if cyc["status"] == "REVISION_NEEDED":
                pkey = "prompt_revise:%s" % cyc["id"]
                if not self.job_done(pkey):
                    pj = self.job(pkey, "prompt_revise", spec["creator"] if name != "proof_of_concept" else
                                  ("image-producer" if cap == "image-generation" else "video-producer"), name, T,
                                  files=[prompt], findings=cyc.get("findings", []),
                                  extra={"reason": "independent review %s rejected %s" % (cyc["id"], out)})
                    self.plan_repair(st, name, spec, T, cset, cyc, acts, waits, [prompt],
                                     prompt_job=pj if self.job_needed(pkey) else None)
                    return False
                first = (cyc.get("findings") or [{}])[0].get("issue", "see review")
                acts.append(("generate", dict(it, input=inp, revision=True,
                                              strategy="prompt revised after %s: %s" % (cyc["id"], str(first)[:120])), name))
                return False
            acts.append(("engine", ("review", "open", "--target", T, "--set", cset, "--creator", self.creator_for(name, cap),
                                    "--files", out), name))
            return False
        if out:
            if out not in self.state["inspections"]:
                acts.append(("internal", ("inspect", out), name))
                return False
            acts.append(("engine", ("review", "open", "--target", T, "--set", cset, "--creator", self.creator_for(name, cap),
                                    "--files", out), name))
            return False
        fails = X.consecutive_failures(X.gen_rows(self.pdir), it["shot"], cap)
        if fails >= 4:
            acts.append(("internal", ("block", T, name, "PROVIDER_FAILING: %d consecutive failed generations for %s" % (fails, cap)), name))
            return False
        strategy = None
        if fails >= 2:
            pkey = "prompt_revise:fail:%s:%d" % (T, fails)
            if not self.job_done(pkey):
                if self.job_needed(pkey):
                    last = [r for r in X.gen_rows(self.pdir) if r["shot_id"] == it["shot"] and r["capability"] == cap][-1]
                    acts.append(self.job(pkey, "prompt_revise", self.creator_for(name, cap), name, T, files=[prompt],
                                         findings=[{"id": last["attempt_id"], "issue": last["error_analysis"]}],
                                         extra={"reason": "%d consecutive provider failures (%s)" % (fails, last["failure_category"])}))
                return False
            strategy = "prompt revised after %d consecutive provider failures" % fails
        acts.append(("generate", dict(it, input=inp, revision=False, strategy=strategy), name))
        return False

    def pick_candidates(self, st, T, cset):
        base = self.state["loop_baseline"].get(T)
        cycles = [r for r in st["reviews"] if r["target"] == T and r["set"] == cset]
        if base and base in [r["id"] for r in cycles]:
            cycles = cycles[[r["id"] for r in cycles].index(base) + 1:]
        return [r for r in cycles if r["status"] in ("REVISION_NEEDED", "NEEDS_HUMAN_REVIEW")
                and not (r.get("human_ack") or {}).get("rejected")]

    def plan_pick(self, st, name, T, cset, cyc, acts, waits):
        """Producer rule (review_loop.pick_best): each media item gets at most max_media_attempts reviewed outputs; then
        an independent reviewer picks the best. Unverified-only reviews go straight to the pick (the reviewer may accept
        or escalate). Only a real problem (escalate / BLOCKER) comes back to the human. Returns True when it planned."""
        rl = self.policy().get("review_loop", {})
        if not rl.get("pick_best") or (cyc.get("human_ack") or {}).get("rejected"):
            return False
        cands = self.pick_candidates(st, T, cset)
        if cyc["status"] == "REVISION_NEEDED" and len(cands) < int(rl.get("max_media_attempts", 2)):
            return False  # the one allowed retry
        key = "pick:%s" % cyc["id"]
        pk = self.state.setdefault("picks", {}).get(cyc["id"])
        if pk and pk.get("escalate"):
            fa = self.state.setdefault("final_attempt", {})
            if rl.get("final_attempt") and (T not in fa or fa[T].get("cyc") == cyc["id"]):
                return "final"  # not yet tried, or the final generation produced no new output yet
            if rl.get("final_attempt"):
                # the final attempt was used and the best of all candidates still has a real problem: set the shot aside
                sid = T.split(":", 1)[1]
                self.state.setdefault("set_aside", {})[sid] = {"target": T, "reason": pk.get("reason", "")[:300], "at": now()}
                self.journal("SET_ASIDE", target=T, reason=pk.get("reason", "")[:300])
                return True
            waits.append({"type": "pick_escalated", "target": T, "stage": name, "reason": pk.get("reason", "")})
            return True
        if self.job_needed(key):
            vfile = "reviews/verdicts/PICK_%s.json" % cyc["id"]
            files = [list(c["files"])[0] for c in cands]
            acts.append(self.job(key, "pick", self.pipe["reviewers"][cset], name, T, files=files, cycle_id=cyc["id"], set=cset,
                                 extra={"verdict": vfile, "evidence": "reviews/evidence/PICK_%s.md" % cyc["id"],
                                        "candidates": [{"file": list(c["files"])[0], "cycle": c["id"], "status": c["status"],
                                                        "findings": c.get("findings", [])} for c in cands],
                                        "allow_tools": list(REVIEW_TOOLS)}))
            return True
        if not self.job_done(key) and self.state["jobs"].get(key, {}).get("status") != "RUNNING":
            waits.append({"type": "blocked", "stage": name, "target": T, "reason": "pick job failed: %s" % key})
        return True

    def creator_for(self, stage, cap):
        if cap == "image-generation":
            return "image-producer"
        if P.cap_kind(cap) == "audio":
            return "sound-producer"
        return "video-producer"

    def plan_media(self, st, name, spec, acts, waits):
        items = self.items(spec["items"], spec.get("phase", "production"))
        if self.plan_items(st, name, spec, items, acts, waits) and not acts:
            acts.append(("engine", ("stage", "done", name), name))

    def plan_proof(self, st, name, spec, acts, waits):
        rows = [r for r in self.shots() if not A.shot_holds(self.pdir, r["shot_id"])][: int(spec.get("proof_shots", 1))]
        if not rows:
            waits.append({"type": "blocked", "stage": name, "reason": "no shot is eligible for the proof (all on hold)"})
            return
        items = self.items("keyframes", "proof", rows) + self.items("shots", "proof", rows)
        before = len(acts)
        if not self.plan_items(st, name, spec, items, acts, waits) or len(acts) > before:
            return
        for r in rows:
            src = self.approved_output(st, "shot:" + r["shot_id"], "shot_media")
            dst = "media/proof_of_concept/PROOF_%s.mp4" % r["shot_id"]
            if src and (not (self.pdir / dst).exists() or sha(self.pdir / dst) != sha(self.pdir / src)):
                acts.append(("internal", ("copy", src, dst), name))
                return
        self.plan_document(st, name, spec, acts, waits)

    # ---- edit / final
    def segments(self, st):
        segs = []
        for r in self.shots():
            v = self.approved_output(st, "shot:" + r["shot_id"], "shot_media")
            if not v:
                return None
            a = self.approved_output(st, "audio:" + r["shot_id"], "dialogue_audio")
            segs.append({"shot_id": r["shot_id"], "video": str(self.pdir / v), "duration": float(r.get("duration_s") or 3),
                         "audio": str(self.pdir / a) if a else None})
        beds = {}
        for bed in self.pipe["beds"]:
            b = self.approved_output(st, "audio:" + bed, "audio_bed")
            if b:
                beds[bed] = str(self.pdir / b)
        return segs, beds

    def plan_edit(self, st, name, spec, acts, waits):
        if not self.doc_ready("EDIT_PLAN.md"):
            key = "create:%s" % name
            if self.job_needed(key):
                acts.append(self.job(key, "create", spec["creator"], name, "gate:GATE-08", files=["EDIT_PLAN.md"]))
            return
        sb = self.segments(st)
        if not sb:
            waits.append({"type": "blocked", "stage": name, "reason": "not every shot has an approved clip"})
            return
        dg = hashlib.sha256((assembly.inputs_digest(*sb) + sha(self.pdir / "EDIT_PLAN.md")).encode()).hexdigest()
        if self.state["renders"].get("rough", {}).get("digest") != dg or not (self.pdir / ROUGH).exists():
            if st["gates"]["GATE-08"]["state"] in ("NOT_STARTED", "PREPARING", "REVISION_REQUESTED", "REJECTED", "STALE"):
                acts.append(("internal", ("render", "rough", dg), name))
                return
        if ROUGH not in self.state["inspections"] or self.state["inspections"].get(ROUGH + "#sha") != sha(self.pdir / ROUGH):
            acts.append(("internal", ("inspect", ROUGH), name))
            return
        self.plan_document(st, name, spec, acts, waits, files=["EDIT_PLAN.md"],
                           reports_about=[ROUGH, "EDIT_PLAN.md"], revise_files=["EDIT_PLAN.md"])

    def plan_final(self, st, name, spec, acts, waits):
        sb = self.segments(st)
        if not sb:
            waits.append({"type": "blocked", "stage": name, "reason": "approved clips missing for final render"})
            return
        dg = assembly.inputs_digest(*sb)
        if self.state["renders"].get("final", {}).get("digest") != dg or not (self.pdir / FINAL).exists():
            acts.append(("internal", ("render", "final", dg), name))
            return
        fs = sha(self.pdir / FINAL)
        if self.state["inspections"].get(FINAL + "#sha") != fs:
            acts.append(("internal", ("inspect", FINAL), name))
            return
        if st.get("delivery_spec") and self.state["renders"]["final"].get("techcheck_sha") != fs:
            acts.append(("internal", ("techcheck", FINAL), name))
            return
        about = [FINAL] + self.state["inspections"].get(FINAL, [])[:1]
        for rep in spec.get("reports", []):
            dg2 = self.digest(about)
            if self.state["reports"].get(rep["file"]) != dg2 or not self.doc_ready(rep["file"]):
                key = "report:%s:%s" % (rep["file"], dg2[:12])
                if self.job_needed(key):
                    acts.append(self.job(key, "report", rep["agent"], name, "stage:" + name, files=[rep["file"]],
                                         extra={"report": rep["file"], "about": about, "digest": dg2}))
                return
        acts.append(("engine", ("stage", "done", name), name))

    # ------------------------------------------------------------------ execution
    def do_engine(self, argv, stage):
        code, out = engine(self.pdir, *argv)
        self.journal("ENGINE", argv=list(argv), code=code, out=out.strip()[-400:])
        if code != 0:
            raise C.StudioError("engine %s failed: %s" % (" ".join(argv), out.strip()[-300:]))
        if argv[0] == "approve":
            self.journal("MILESTONE", gate=argv[1], by="policy")
            pipe_stage = next((n for n in C.STAGE_NAMES if C.STAGE_BY[n][3] == argv[1]), None)
            if pipe_stage and self.pipe["stages"][pipe_stage].get("export_on_approve"):
                self.deliver()
        engine(self.pdir, "log", "--kind", "ACTUAL", "--stage", stage, "--action", ("orchestrator: " + " ".join(argv))[:180],
               "--owner", "orchestrator", "--status", "DONE")
        return out

    def do_generate(self, it, stage):
        if it.get("final_of"):  # count final attempts only when one is actually issued (plan() runs every loop)
            fa = self.state.setdefault("final_attempt", {})
            n = fa.get(it["target"], {}).get("n", 0)
            fa[it["target"]] = {"cyc": it["final_of"], "n": n + 1}
        pol = self.policy()
        argv = ["generate", "--shot", it["shot"], "--capability", it["cap"], "--phase", it["phase"], "--units", it["units"]]
        if it.get("input"):
            argv += ["--input", it["input"]]
        if it.get("revision"):
            argv += ["--revision"]
        if it.get("strategy"):
            argv += ["--strategy-change", it["strategy"]]
        auth = pol.get("spending", {}).get("authorization") if pol.get("spending", {}).get("unattended_paid_generation") else None
        if auth:
            argv += ["--authorization", auth]
        cfg = P.load_config(self.pdir)
        name, prov, model = P.resolve(cfg, it["cap"], it.get("shot_type"))
        ranking = P.rank(cfg, it["cap"], self.pdir, it["phase"])
        self.journal("PROVIDER_DECISION", target=it["target"], capability=it["cap"], provider=name, model=model,
                     basis="configured route%s" % (" by shot type" if it.get("shot_type") in (cfg["routes"][it["cap"]].get("by_shot_type") or {}) else ""),
                     evidence_ranking=[{k: r[k] for k in ("provider", "score", "eligible", "problems", "unknown_metrics")} for r in ranking])
        if prov["type"] == "connector":
            argv += ["--connector-verified"]
        code, out = engine(self.pdir, *argv)
        self.journal("GENERATE", argv=[str(a) for a in argv], code=code, out=out.strip()[-500:])
        wk = self.wait_key()
        if code == 0 and "PREFLIGHT_OK" in out:
            n = len([r for r in X.gen_rows(self.pdir) if r["shot_id"] == it["shot"]]) + 1
            kind = P.cap_kind(it["cap"])
            vers = len(list((self.pdir / "media" / P.KIND_DIR[kind] / it["shot"]).glob("*"))) + 1
            outrel = "media/%s/%s/%s%s_v%02d%s" % (P.KIND_DIR[kind], it["shot"], it["shot"], P.CAP_TAG.get(it["cap"], ""), vers, P.KIND_EXT[kind])
            est = P.estimate(prov, it["cap"], it["units"])
            self.state["connector_jobs"] = self.state.get("connector_jobs", {})
            key = "connector:%s:%d" % (it["target"], n)
            return ("job", {"key": key, "kind": "connector", "agent": self.creator_for(stage, it["cap"]), "stage": stage,
                            "target": it["target"], "files": [],
                            "extra": {"tools": prov.get("mcp_tools", []), "allow_tools": prov.get("mcp_tools", []) + [
                                "Bash(studio/bin/attempt-record *)",
                                "Bash(curl -fsSL -o projects/%s/media/*)" % self.pid,
                                "Bash(cp projects/%s/media/*)" % self.pid, "Bash(mkdir -p projects/%s/media/*)" % self.pid] + prov.get("extra_allow", []),
                                "capability": it["cap"], "model": model, "provider": name,
                                "prompt": P.default_prompt(it["cap"], it["shot"]), "input": it.get("input"),
                                "price": est, "authorization": auth, "output": outrel, "shot": it["shot"], "phase": it["phase"],
                                "rows_before": len(X.gen_rows(self.pdir)), "mcp_config": prov.get("mcp_config"), "mcp_strict": prov.get("mcp_strict"), "add_dirs": prov.get("add_dirs", []),
                                "instructions": prov.get("instructions")}})
        if code == 0 or code == 1:
            self.state["item_wait"].pop(it["target"], None)
            return None
        reason = out.strip().splitlines()[-1] if out.strip() else "generate failed"
        typ = ("spending" if "AUTHORIZATION" in out else "integration" if ("PRICE_UNKNOWN" in out or "MISSING" in out or
               "BLOCKED_EXTERNAL_TOOL" in out or code == 3) else "hold" if "HOLD_ACTIVE" in out else "blocked")
        self.state["item_wait"][it["target"]] = {"type": typ, "reason": reason[:300], "key": wk}
        self.save()
        return None

    def do_internal(self, op, stage):
        kind = op[0]
        if kind == "block":
            _, target, stg, reason = op
            self.state["blocked"][target] = {"reason": reason, "at": now(), "stage": stg}
            if target.startswith(("gate:", "stage:")):
                engine(self.pdir, "stage", "block", stg, "--reason", reason[:200])
            self.journal("BLOCKED", target=target, reason=reason)
        elif kind == "inspect":
            rel = op[1]
            code, out = engine(self.pdir, "inspect", rel, "--frames", "4")
            ev = [l.split("evidence dir: ", 1)[1].strip() for l in out.splitlines() if l.startswith("evidence dir: ")]
            self.state["inspections"][rel] = [e + "/inspection.json" for e in ev]
            self.state["inspections"][rel + "#sha"] = sha(self.pdir / rel)
            self.journal("INSPECT", file=rel, code=code, evidence=ev)
            if code != 0:
                self.state["blocked"]["inspect:" + rel] = {"reason": "INSPECTION_UNAVAILABLE: " + out[-200:], "at": now(), "stage": stage}
        elif kind == "copy":
            _, src, dst = op
            (self.pdir / dst).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.pdir / src, self.pdir / dst)
            self.journal("COPY", src=src, dst=dst)
        elif kind == "render":
            _, which, dg = op
            st = self.st()
            segs, beds = self.segments(st)
            out = ROUGH if which == "rough" else FINAL
            if (self.pdir / out).exists():
                arch = self.pdir / "versions" / ("render_%s_%s" % (which, now().replace(":", "")))
                arch.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self.pdir / out, arch / Path(out).name)
            spec = st.get("delivery_spec") or {"width": 1280, "height": 720, "fps": 24}
            man = assembly.render(segs, beds, self.pdir / out, spec, final=(which == "final"),
                                  workdir=self.odir / ("render_tmp_%s" % which))
            man["digest"] = dg
            self.state["renders"][which] = man
            self.journal("RENDER", which=which, output=out, sha=man["sha256"], duration=man["duration_s"])
        elif kind == "estimate":
            _, stg, lines = op
            cfg = P.load_config(self.pdir)
            for cap, item, units in lines:
                pname, prov, model = P.resolve(cfg, cap)
                engine(self.pdir, "cost", "add", "--kind", "ESTIMATE", "--stage", stg, "--item", "%s via %s/%s" % (item, pname, model),
                       "--units", units, "--unit-cost", P.unit_price(prov, cap), "--notes", "price from providers.json (%s)" % cap)
            self.journal("ESTIMATE", lines=lines)
        elif kind == "techcheck":
            code, out = engine(self.pdir, "techcheck", op[1])
            self.state["renders"]["final"]["techcheck_sha"] = sha(self.pdir / op[1])
            self.state["renders"]["final"]["techcheck"] = {"code": code, "out": out[-600:]}
            ev = [l.split("evidence: ", 1)[1].strip() for l in out.splitlines() if l.startswith("evidence: ")]
            self.state["inspections"].setdefault(FINAL, []).extend(ev)
            self.journal("TECHCHECK", file=op[1], code=code)
        self.save()

    def archive_before(self, job):
        if job["kind"] not in ("revise", "prompt_revise"):
            return
        dest = self.pdir / "versions" / ("%s_before_%s" % (job["target"].replace(":", "_"), job["key"].replace(":", "_").replace("/", "_")))
        for f in job["files"]:
            if (self.pdir / f).exists() and not (dest / f).exists():
                (dest / f).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(self.pdir / f, dest / f)

    def exec_job(self, job):
        key = job["key"]
        with ENGINE_LOCK:
            rec = self.state["jobs"].setdefault(key, {"key": key, "kind": job["kind"], "agent": job["agent"],
                                                      "stage": job["stage"], "target": job["target"], "attempts": 0})
            rec.update(status="RUNNING", attempts=rec["attempts"] + 1, started_at=now(), pid=os.getpid())
            self.save()
            self.journal("JOB_START", job=key, agent=job["agent"], attempt=rec["attempts"])
            pre = {f: (sha(self.pdir / f) if (self.pdir / f).exists() else None) for f in job["files"]}
            self.archive_before(job)
        jdir = self.odir / "jobs" / key.replace(":", "_").replace("/", "_")
        jdir.mkdir(parents=True, exist_ok=True)
        if job.get("retry_note"):
            job.setdefault("extra", {})["retry_note"] = job["retry_note"]
        try:
            if isinstance(self.backend, B.ClaudeBackend):
                res = self.backend.run(job, self.pdir, self.pid, self.crit, job_dir=jdir)
            else:
                res = self.backend.run(job, self.pdir, self.pid, self.crit)
        except Exception as e:  # noqa - a backend crash is a failed job, never success
            res = {"status": "FAILED", "cost_usd": 0.0, "summary": "backend exception: %s" % e}
        with ENGINE_LOCK:
            self.post_job(job, rec, res, pre)

    def post_job(self, job, rec, res, pre):
        key = job["key"]
        cost = float(res.get("cost_usd") or 0)
        self.state["llm_cost_usd"] = round(self.state.get("llm_cost_usd", 0) + cost, 4)
        rec.update(ended_at=now(), cost_usd=round(rec.get("cost_usd", 0) + cost, 4), summary=res.get("summary", "")[:600],
                   backend=self.backend.name)
        if res["status"] == "TRANSIENT":
            rec["status"] = "TRANSIENT"
            rec["attempts"] -= 1
            self.state["retry_at"] = (datetime.datetime.utcnow() + datetime.timedelta(seconds=int(res.get("retry_after_s", 900)))
                                      ).replace(microsecond=0).isoformat() + "Z"
            self.journal("JOB_TRANSIENT", job=key, retry_at=self.state["retry_at"], summary=res.get("summary"))
            self.save()
            return
        ok, why = res["status"] == "OK", res.get("summary", "")
        if ok:
            ok, why = self.verify_job(job, pre)
        rec["status"] = "DONE" if ok else "FAILED"
        rec["verify"] = why
        self.journal("JOB_END", job=key, status=rec["status"], cost_usd=cost, verify=why)
        engine(self.pdir, "log", "--kind", "ACTUAL", "--stage", job["stage"] if job["stage"] in C.STAGE_BY else "research",
               "--action", ("%s %s by %s" % (job["kind"], job["target"], job["agent"]))[:180], "--owner", job["agent"],
               "--status", rec["status"], "--tool", "claude-code:%s" % self.backend.name, "--cost", "%.4f" % cost,
               "--details", (why or "")[:200])
        lim = self.policy().get("review_loop", {}).get("max_job_retries", 2) + 1
        if not ok and rec["attempts"] >= lim:
            rec["status"] = "BLOCKED"
            self.state["blocked"][job["target"]] = {"reason": "JOB_FAILED %s after %d attempts: %s" % (key, rec["attempts"], why[:200]),
                                                    "at": now(), "stage": job["stage"]}
            if job["target"].startswith(("gate:", "stage:")) and job["stage"] in C.STAGE_BY:
                engine(self.pdir, "stage", "block", job["stage"], "--reason", ("JOB_FAILED %s" % key)[:200])
        if ok and job["kind"] == "intake":
            pol = self.policy()
            pol["scope_lock"].update(sha256=sha(self.pdir / "PROJECT.md"), by="film brief (%s)" % self.state["intake"].get("by", "user"),
                                     at=now())
            A.save_policy(self.pdir, pol)
        if ok and job["kind"] == "revise":
            for f in job["files"]:
                if (self.pdir / f).exists() and pre.get(f) != sha(self.pdir / f):
                    engine(self.pdir, "changelog", "--file", f, "--by", job["agent"],
                           "--summary", "orchestrator revision after %s" % job.get("cycle_id"))
        self.save()

    def verify_job(self, job, pre):
        k = job["kind"]
        if k in ("create", "intake"):
            bad = [f for f in job["files"] if not self.doc_ready(f)]
            return (not bad, "deliverables ready" if not bad else "not ready: %s" % ", ".join(bad))
        if k in ("revise", "prompt_revise"):
            changed = [f for f in job["files"] if (self.pdir / f).exists() and sha(self.pdir / f) != pre.get(f)]
            if not changed:
                return False, "no file changed"
            bad = [f for f in job["files"] if k == "revise" and not self.doc_ready(f)]
            return (not bad, "changed %s" % ", ".join(changed) if not bad else "not ready after revision: %s" % bad)
        if k == "report":
            rel = job["extra"]["report"]
            if not self.doc_ready(rel):
                return False, "report missing or placeholder"
            self.state["reports"][rel] = job["extra"]["digest"]
            return True, "report written"
        if k == "review":
            vf = self.pdir / job["extra"]["verdict"]
            if not vf.exists():
                return False, "no verdict file"
            code, out = engine(self.pdir, "review", "close", job["cycle_id"], "--reviewer", job["agent"], "--verdict-file", str(vf))
            if code != 0:
                if "VOID" in out:
                    return True, "cycle VOID (deliverable changed during review) - will reopen"
                job["retry_note"] = out[-400:]
                return False, "engine rejected verdict: %s" % out.strip()[-300:]
            return True, out.strip().splitlines()[0] if out.strip() else "closed"
        if k == "pick":
            vf = self.pdir / job["extra"]["verdict"]
            if not vf.exists():
                return False, "no pick verdict file"
            try:
                v = json.loads(vf.read_text(encoding="utf-8"))
            except ValueError as e:
                return False, "pick verdict is not JSON: %s" % e
            if v.get("escalate"):
                self.state.setdefault("picks", {})[job["cycle_id"]] = {"escalate": True, "reason": str(v.get("reason", ""))[:400],
                                                                     "at": now()}
                return True, "ESCALATED to human: %s" % str(v.get("reason", ""))[:200]
            code, out = engine(self.pdir, "review", "pick", "--target", job["target"], "--set", job["set"],
                               "--reviewer", job["agent"], "--verdict-file", str(vf))
            if code != 0:
                job["retry_note"] = out[-400:]
                if "BLOCKER_PRESENT" in out or "ESCALATED" in out:
                    self.state.setdefault("picks", {})[job["cycle_id"]] = {"escalate": True, "reason": out.strip()[-300:], "at": now()}
                    return True, "ESCALATED to human: %s" % out.strip()[-200:]
                return False, "engine rejected pick: %s" % out.strip()[-300:]
            return True, out.strip().splitlines()[-1] if out.strip() else "picked"
        if k == "diagnose":
            return self.doc_ready(job["extra"]["report"]), "diagnosis written"
        if k == "connector":
            rows = X.gen_rows(self.pdir)[job["extra"]["rows_before"]:]
            mine = [r for r in rows if r["shot_id"] == job["extra"]["shot"] and r["capability"] == job["extra"]["capability"]]
            if not mine:
                return False, "agent did not record an attempt"
            return True, "attempt %s %s" % (mine[-1]["attempt_id"], mine[-1]["outcome"])
        return False, "unknown job kind"

    def deliver(self):
        st = self.st()
        if st["gates"]["GATE-09"]["state"] != "APPROVED":
            return None
        code, out = engine(self.pdir, "export")
        path = out.strip().split("Export: ", 1)[-1].split("  (")[0] if "Export: " in out else None
        final = self.pdir / FINAL
        self.state["delivered"] = {"at": now(), "export": path, "final": str(final),
                                   "final_sha256": sha(final) if final.exists() else None,
                                   "synthetic": self.backend.name == "mock" or self.synthetic_inputs()}
        self.journal("DELIVERED", **self.state["delivered"])
        self.save()
        return self.state["delivered"]

    def synthetic_inputs(self):
        return any(r["synthetic"] == "yes" for r in X.gen_rows(self.pdir))

    # ------------------------------------------------------------------ main loop
    def run(self, max_jobs=None, daemon=False, poll_s=60):
        self.acquire()
        signal.signal(signal.SIGTERM, lambda *a: setattr(self, "stop_flag", True))
        try:
            self.recover()
            self.state["status"] = "RUNNING"
            self.state["runner_pid"] = os.getpid()
            self.save()
            self.journal("RUN_START", backend=self.backend.name, daemon=daemon)
            done_jobs = 0
            guard = 0
            inflight = {}
            genpool = ThreadPoolExecutor(max_workers=1)
            jobpool = ThreadPoolExecutor(max_workers=max(1, self.parallel))
            while True:
                if self.stop_flag:
                    self.state["status"] = "STOPPED"
                    break
                if (self.odir / "PAUSE").exists():
                    self.state["status"] = "PAUSED"
                    break
                ra = self.state.get("retry_at")
                if ra and ra > now():
                    self.state["status"] = "WAITING_RATE_LIMIT"
                    with ENGINE_LOCK:
                        self.save()
                    if not daemon:
                        break
                    time.sleep(min(poll_s, 30))
                    continue
                self.state["retry_at"] = None
                llm_cap = self.policy().get("spending", {}).get("llm_budget_usd")
                if llm_cap is not None and self.state.get("llm_cost_usd", 0) >= float(llm_cap):
                    self.state["status"] = "WAITING_HUMAN"
                    self.state["waiting"] = [{"type": "compute_budget", "reason": "agent compute budget %.2f reached" % float(llm_cap)}]
                    break
                if llm_cap is not None and hasattr(self.backend, "cap"):
                    per_job = float(self.policy().get("spending", {}).get("per_job_llm_cap_usd", 15) or 15)
                    left = float(llm_cap) - float(self.state.get("llm_cost_usd", 0))
                    self.backend.cap = round(max(0.5, min(per_job, left)), 2)
                done_jobs += self._reap(inflight)
                if max_jobs and done_jobs >= max_jobs:
                    self.state["status"] = "IDLE"
                    break
                with ENGINE_LOCK:
                    acts, waits = self.plan()
                    self.state["waiting"] = waits
                busy = {j["target"] for j in inflight.values()}
                gen_busy = any(j["kind"] == "connector" for j in inflight.values())

                def tgt_of(a):
                    return a[1].get("target") if a[0] in ("job", "generate") else None
                acts = [a for a in acts if tgt_of(a) not in busy]
                quick = [a for a in acts if a[0] in ("engine", "internal")]
                if quick:
                    guard += 1
                    if guard > 500:
                        raise C.StudioError("NO_PROGRESS_GUARD: too many engine steps without a job; see journal")
                    a = quick[0]
                    try:
                        with ENGINE_LOCK:
                            if a[0] == "engine":
                                self.do_engine(a[1], a[2])
                            else:
                                self.do_internal(a[1], a[2])
                    except C.StudioError as e:
                        tgt = a[1][1] if a[0] == "internal" else self.stage_target(a[2])
                        self.state["blocked"][tgt] = {"reason": str(e)[:300], "at": now(), "stage": a[2]}
                        self.journal("STEP_FAILED", action=str(a)[:300], error=str(e)[:400])
                    except assembly.RenderError as e:
                        self.state["blocked"]["render"] = {"reason": "RENDER_FAILED: %s" % str(e)[:300], "at": now(), "stage": a[2]}
                    with ENGINE_LOCK:
                        self.save()
                    continue
                guard = 0
                started = False
                # lane 1: one generation at a time (a single Flow browser session)
                gens = [a for a in acts if a[0] == "generate"]
                if gens and not gen_busy:
                    last = getattr(self, "_last_gen", "")
                    other = [g for g in gens if not g[1].get("target", "").startswith(last)] if last else []
                    a = other[0] if other else gens[0]
                    self._last_gen = a[1].get("target", "").split(":")[0] + ":"
                    try:
                        with ENGINE_LOCK:
                            j = self.do_generate(a[1], a[2])
                    except C.StudioError as e:
                        with ENGINE_LOCK:
                            self.state["blocked"][a[1].get("target")] = {"reason": str(e)[:300], "at": now(), "stage": a[2]}
                            self.journal("STEP_FAILED", action=str(a)[:300], error=str(e)[:400])
                            self.save()
                        continue
                    if not j:
                        continue  # preflight changed state (wait recorded); re-plan
                    inflight[genpool.submit(self.exec_job, j[1])] = j[1]
                    busy.add(j[1]["target"])
                    started = True
                # lane 2: agent jobs (reviews, picks, prompt revisions, writing) up to `parallel` at once
                free = max(1, self.parallel) - len([1 for j in inflight.values() if j["kind"] != "connector"])
                seen = set(busy)
                for a in acts:
                    if free <= 0:
                        break
                    if a[0] != "job" or a[1]["target"] in seen:
                        continue
                    seen.add(a[1]["target"])
                    inflight[jobpool.submit(self.exec_job, a[1])] = a[1]
                    free -= 1
                    started = True
                if started:
                    continue
                if inflight:
                    wait(list(inflight), timeout=30, return_when=FIRST_COMPLETED)
                    continue
                st = self.st()
                if st["gates"]["GATE-09"]["state"] == "APPROVED":
                    if not self.state.get("delivered"):
                        self.deliver()
                    self.state["status"] = "DELIVERED"
                    break
                self.state["status"] = "WAITING_HUMAN" if any(w["type"] != "blocked" for w in waits) else (
                    "BLOCKED" if waits else "IDLE")
                self.save()
                if daemon and self.state["status"] == "WAITING_HUMAN":
                    for _ in range(int(poll_s)):
                        if self.stop_flag or (self.odir / "PAUSE").exists():
                            break
                        time.sleep(1)
                    continue
                break
            if inflight:
                wait(list(inflight))
                done_jobs += self._reap(inflight)
            genpool.shutdown(wait=True)
            jobpool.shutdown(wait=True)
            with ENGINE_LOCK:
                self.save()
            self.journal("RUN_END", status=self.state["status"], jobs=done_jobs)
            return self.state["status"]
        finally:
            self.release()

    def _reap(self, inflight):
        n = 0
        for f in [f for f in inflight if f.done()]:
            job = inflight.pop(f)
            n += 1
            try:
                f.result()
            except Exception as e:  # noqa - exec_job already records failures; never kill the runner
                self.journal("JOB_CRASH", job=job["key"], error=str(e)[:300])
        return n


# ------------------------------------------------------------------ reporting (Egyptian Arabic, no internal IDs)
def human_waits(o, waits):
    st = o.st()
    msgs, seen = [], set()
    for w in waits:
        t = w["type"]
        if t == "gate_approval":
            name = st["gates"][w["gate"]]["name_ar"]
            m = "«%s» جاهزة ومستنية موافقتك." % name
            if w.get("external"):
                m += " وقبل الموافقة لازم تتسجّل مراجعة المستشار الخارجي (مفيش مستشار متعيّن لحد دلوقتي)."
            if w["gate"] == "GATE-07":
                m += " مع الموافقة محتاج تحدد الميزانية وتدّي تصريح صرف للتوليد."
            if w.get("policy_problems"):
                m += " (الموافقة التلقائية مش متاحة: %s)" % w["policy_problems"][0].split(":")[0]
        elif t == "pick_escalated":
            m = "المراجع شاف مشكلة حقيقية في «%s» بعد الإعادة ومحتاج قرارك: %s" % (w.get("target", ""), w.get("reason", "")[:200])
        elif t == "human_ack":
            m = "المراجع مقدرش يتأكد من حاجات في «%s» بنفسه — محتاج تشوفها وتأكد." % w.get("target", "")
        elif t == "hold":
            m = "في لقطات فيها إصابات صريحة مستنية موافقة العميل الموثّقة (%s) — باقي الشغل ماشي." % w["hold"]
        elif t == "spending":
            m = "التوليد المدفوع محتاج تصريح صرف منك قبل ما يبدأ."
        elif t == "integration":
            m = "مفيش أداة متوصّلة ومُعتمدة لـ %s — محتاجين نوصّل مزوّد (التفاصيل في docs/INTEGRATIONS.md)." % w.get("capability", w.get("reason", ""))
        elif t == "compute_budget":
            m = "ميزانية تشغيل الوكلاء خلصت — محتاج تزوّدها عشان نكمّل."
        else:
            m = "متوقف في «%s»: %s" % (STAGE_AR.get(w.get("stage"), w.get("stage")), w.get("reason", ""))
        if m not in seen:
            seen.add(m)
            msgs.append(m)
    return msgs


def status_text(o):
    st = o.st()
    acts, waits = o.plan()
    cur = next((n for n in C.STAGE_NAMES if st["stages"][n]["state"] == "IN_PROGRESS"), None)
    done = [st["gates"][g]["name_ar"] for g in C.GATE_IDS if st["gates"][g]["state"] == "APPROVED"]
    est, act = C.cost_totals(o.pdir)
    b = st["budget"]
    lines = ["🎬 %s — %s" % (st["project_id"], st["title"]),
             "الحالة: %s" % {"RUNNING": "شغّال", "PAUSED": "متوقف مؤقتاً", "WAITING_HUMAN": "مستني قرار منك",
                             "BLOCKED": "متعطّل", "DELIVERED": "اتسلّم", "WAITING_RATE_LIMIT": "مستني حد الاستخدام يرجع",
                             "STOPPED": "متوقف", "IDLE": "واقف"}.get(o.state.get("status"), o.state.get("status")),
             "المرحلة دلوقتي: %s" % (STAGE_AR.get(cur, cur) if cur else "—"),
             "اللي اتعمد: %s" % ("، ".join(done) if done else "لسه مفيش")]
    lines.append("التكلفة: تشغيل الوكلاء %.2f$ فعلي | التوليد: تقديري %.2f$ / فعلي %.2f$ | الميزانية: %s" % (
        o.state.get("llm_cost_usd", 0), est, act, ("%.2f$" % b["approved_total"]) if b["approved_total"] is not None else "مش متحددة"))
    msgs = human_waits(o, waits)
    if msgs:
        lines.append("محتاج منك:")
        lines += ["  • " + m for m in msgs]
    nxt = [a for a in acts if a[0] in ("job", "engine", "generate", "internal")]
    if nxt and not msgs:
        lines.append("الخطوة الجاية: %s" % STAGE_AR.get(nxt[0][2] if nxt[0][0] != "job" else nxt[0][1]["stage"], ""))
    if o.state.get("delivered"):
        d = o.state["delivered"]
        lines.append("الفيلم النهائي: %s%s" % (d["final"], " (ملف اختبار اصطناعي — مش إنتاج حقيقي)" if d.get("synthetic") else ""))
    return "\n".join(lines)


def shot_report(o):
    st = o.st()
    gen = X.gen_rows(o.pdir)
    out = []
    for r in o.shots():
        sid = r["shot_id"]
        rec = {"shot_id": sid, "engine_status": r["status"], "duration_s": r.get("duration_s"),
               "holds": [h["id"] for h in A.shot_holds(o.pdir, sid)], "items": {}}
        for target, cset in (("keyframe:" + sid, "shot_media"), ("shot:" + sid, "shot_media"), ("audio:" + sid, "dialogue_audio")):
            cyc = X.latest_cycle(st, target, cset)
            kind = target.split(":")[0]
            attempts = [a for a in gen if a["shot_id"] == sid and
                        ((kind == "keyframe" and a["capability"] == "image-generation") or
                         (kind == "shot" and P.cap_kind(a["capability"]) == "video") or
                         (kind == "audio" and a["capability"] == "speech-generation"))]
            rec["items"][kind] = {"attempts": [{k: a[k] for k in ("attempt_id", "provider", "model", "outcome", "output_path",
                                                                  "synthetic", "strategy_change", "cost_usd")} for a in attempts],
                                  "prompt": P.default_prompt({"keyframe": "image-generation", "shot": "video-generation",
                                                              "audio": "speech-generation"}[kind], sid),
                                  "ref_assets": r.get("ref_assets"),
                                  "review": {"cycle": cyc["id"], "status": cyc["status"]} if cyc else None,
                                  "approved_output": o.approved_output(st, target, cset)}
        out.append(rec)
    (o.odir / "SHOT_STATUS.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


# ------------------------------------------------------------------ CLI
def _orch(args, backend=None):
    pdir = C.project_dir(args)
    o = Orchestrator(pdir, backend=backend, parallel=getattr(args, "parallel", None))
    return o


def start_daemon(pdir):
    o = Orchestrator(pdir)
    lockf = open(o.odir / "run.lock", "a+")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(lockf, fcntl.LOCK_UN)
    except OSError:
        return None
    finally:
        lockf.close()
    log = open(o.odir / "runner.log", "a")
    p = subprocess.Popen([sys.executable, str(C.STUDIO_ROOT / "studio" / "studio.py"), "--projects-dir", str(Path(pdir).parent),
                          "film", "run", "--daemon", "-p", Path(pdir).name], stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                         start_new_session=True, cwd=str(C.STUDIO_ROOT))
    return p.pid


def cmd_film(args):
    act = args.action
    if act == "new":
        if not args.id or not (args.brief or args.title):
            raise C.StudioError("film new needs --id and --brief (and optionally --title/--runtime)")
        base = C.projects_dir(args)
        if (base / args.id / "PROJECT_STATUS.json").exists():
            raise C.StudioError("project %s already exists: use `film resume -p %s`" % (args.id, args.id))
        argv = ["--projects-dir", str(base), "init", "--id", args.id, "--title", args.title or args.brief[:60],
                "--runtime-minutes", str(args.runtime or 5), "--concept-approved", "--note", "Brief: %s" % (args.brief or "")]
        code = C.main(argv)
        if code != 0:
            raise C.StudioError("init failed")
        pdir = base / args.id
        over = {}
        if args.client_gates:
            over["client_approval_gates"] = [g.strip().upper() for g in args.client_gates.split(",") if g.strip()]
        A.install_policy(pdir, over)
        pm = pdir / "PROJECT.md"
        if pm.exists():
            human = sorted(set(["GATE-07"] + over.get("client_approval_gates", [])))
            pm.write_text(pm.read_text(encoding="utf-8").replace(
                "| Client approval | Required at major milestones (GATE-01..09) |",
                "| Approvals | AUTO_APPROVAL policy (approval_policy.json): human approval required for %s; other gates "
                "approved by policy only after accepted independent review |" % ", ".join(human)), encoding="utf-8")
        if args.backend == "mock":
            (pdir / "providers.json").write_text(json.dumps(P.mock_config(), indent=1), encoding="utf-8")
        o = Orchestrator(pdir)
        o.state.update(backend=args.backend or "claude", mock_plan=json.loads(args.mock_plan) if args.mock_plan else {},
                       intake={"brief": args.brief or args.title, "by": args.by or "user", "at": now()},
                       parallel=args.parallel or 1)
        o.save()
        o.journal("FILM_NEW", brief=args.brief, backend=o.state["backend"])
        print("Project %s created; production plan ready (%d stages, 9 gates)." % (args.id, len(C.STAGE_NAMES)))
        if args.start:
            pidn = start_daemon(pdir)
            print("runner started (pid %s)" % pidn)
        return 0
    pdir = C.project_dir(args)
    if act in ("run",):
        o = _orch(args)
        if args.backend:
            o.state["backend"] = args.backend
            o.backend = o._make_backend()
        res = o.run(max_jobs=args.max_jobs, daemon=args.daemon, poll_s=args.poll or 60)
        print(status_text(o))
        return 0 if res in ("DELIVERED", "WAITING_HUMAN", "IDLE", "PAUSED", "STOPPED", "WAITING_RATE_LIMIT") else 1
    if act in ("start", "resume"):
        if not A.policy_path(pdir).exists():
            raise C.StudioError("no approval_policy.json in %s; a human must install one (`policy` commands)" % pdir.name)
        o = Orchestrator(pdir)
        if o.state.get("backend") == "claude" and not B.find_claude():
            raise C.StudioError("NO_CLAUDE_CLI: Claude Code CLI not found; set STUDIO_CLAUDE_BIN")
        try:
            (o.odir / "PAUSE").unlink()
        except OSError:
            pass
        if args.foreground:
            res = o.run(max_jobs=args.max_jobs, daemon=False)
            print(status_text(o))
            return 0
        pidn = start_daemon(pdir)
        print("runner %s" % ("started (pid %s)" % pidn if pidn else "already running"))
        return 0
    o = Orchestrator(pdir)
    if act == "pause":
        (o.odir / "PAUSE").write_text(now())
        print("pause requested: the runner stops after the current job")
        return 0
    if act == "stop":
        pf = o.odir / "run.pid"
        if not pf.exists():
            print("no runner")
            return 0
        pidn = int(pf.read_text().strip())
        try:
            if args.now:
                os.killpg(os.getpgid(pidn), signal.SIGKILL)
            else:
                os.kill(pidn, signal.SIGTERM)
            print("stop signal sent to %s" % pidn)
        except ProcessLookupError:
            print("runner %s not alive (stale pid)" % pidn)
        return 0
    if act == "status":
        print(status_text(o))
        if args.verbose:
            acts, waits = o.plan()
            print(json.dumps({"next_actions": [a[1] if a[0] == "job" else list(a[1]) if isinstance(a[1], tuple) else a[1]
                                               for a in acts][:10], "waits": waits,
                              "jobs": {k: v["status"] for k, v in o.state["jobs"].items()}}, indent=1, default=str))
        return 0
    if act == "plan":
        acts, waits = o.plan()
        for a in acts:
            print("NEXT  %s" % (a[1]["key"] + " -> " + a[1]["agent"] if a[0] == "job" else "%s %s" % (a[0], a[1] if not isinstance(a[1], dict) else a[1]["target"])))
        for w in waits:
            print("WAIT  %s" % json.dumps(w, ensure_ascii=False))
        return 0
    if act == "cost":
        st = o.st()
        est, actual = C.cost_totals(pdir)
        jobs = o.state["jobs"].values()
        print("Agent compute (actual, from Claude Code results): %.4f USD over %d jobs" % (o.state.get("llm_cost_usd", 0), len(list(jobs))))
        print("Generation: estimate %.2f | actual %.2f | approved budget %s | authorizations %s" % (
            est, actual, st["budget"]["approved_total"],
            ", ".join("%s %.2f/%.2f" % (a["id"], a["consumed"], a["amount"]) for a in st["authorizations"]) or "none"))
        return 0
    if act == "deliver":
        d = o.deliver()
        if not d:
            print("Not deliverable yet: final film delivery (GATE-09) is %s" % o.st()["gates"]["GATE-09"]["state"])
            return 1
        print(json.dumps(d, indent=1))
        return 0
    if act == "unblock":
        if not args.target or not args.by:
            raise C.StudioError("film unblock needs --target and --by <human>")
        b = o.state["blocked"].pop(args.target, None)
        o.state["item_wait"].pop(args.target, None)
        st = o.st()
        last = [r["id"] for r in st["reviews"] if r["target"] == args.target]
        if last:
            o.state["loop_baseline"][args.target] = last[-1]
        if b and b.get("stage") and st["stages"].get(b["stage"], {}).get("state", "").startswith("BLOCKED"):
            engine(pdir, "stage", "unblock", b["stage"])
        for k, j in o.state["jobs"].items():
            if j["target"] == args.target and j["status"] == "BLOCKED":
                j["status"], j["attempts"] = "FAILED", 0
        o.save()
        o.journal("UNBLOCK", target=args.target, by=args.by, note=args.note)
        print("unblocked %s" % args.target)
        return 0
    if act == "shots":
        print(json.dumps(shot_report(o), indent=1))
        return 0
    if act == "integrations":
        cfg = P.load_config(pdir)
        for cap in P.GENERATION_CAPS:
            name, prov, model = P.resolve(cfg, cap)
            stat, detail = P.availability(prov)
            print("%-18s -> %-14s %-22s %s" % (cap, name, stat, detail))
        return 0
    raise C.StudioError("unknown film action")


def register(core, add):
    global C, X
    C = core
    X = core.ext
    sp = add("film", cmd_film)
    sp.add_argument("action", choices=["new", "resume", "start", "run", "pause", "stop", "status", "plan", "cost", "deliver",
                                       "unblock", "shots", "integrations"])
    sp.add_argument("--brief")
    sp.add_argument("--id")
    sp.add_argument("--title")
    sp.add_argument("--runtime", type=float)
    sp.add_argument("--backend", choices=["claude", "mock"])
    sp.add_argument("--mock-plan")
    sp.add_argument("--client-gates")
    sp.add_argument("--by")
    sp.add_argument("--target")
    sp.add_argument("--note")
    sp.add_argument("--max-jobs", type=int)
    sp.add_argument("--parallel", type=int)
    sp.add_argument("--poll", type=int)
    sp.add_argument("--daemon", action="store_true")
    sp.add_argument("--foreground", action="store_true")
    sp.add_argument("--start", action="store_true")
    sp.add_argument("--now", action="store_true")
    sp.add_argument("--verbose", action="store_true")

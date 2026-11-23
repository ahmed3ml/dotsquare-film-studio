"""Quality-first extensions for studio.py: independent review cycles with acceptance criteria, adaptive retry with
error analysis, per-batch cost authorization, provider routing, direct media inspection, economics reporting,
model policy, team registry checks and the write-guard hook. Receives the core module via register()."""
import csv
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import providers  # noqa: E402
import autonomy  # noqa: E402

C = None  # core module (studio.py)
MEDIA_EXT = (".mp4", ".mov", ".webm", ".png", ".jpg", ".jpeg", ".wav", ".mp3", ".m4a")


# ------------------------------------------------------------------ criteria & reviews
def criteria():
    return json.loads((C.STUDIO_ROOT / "standards" / "criteria.json").read_text(encoding="utf-8"))


def criteria_set(name):
    sets = criteria()["sets"]
    if name not in sets:
        raise C.StudioError("Unknown criteria set %r (%s)" % (name, ", ".join(sets)))
    return sets[name]


def files_unchanged(pdir, files):
    return all((Path(pdir) / rel).exists() and C.lock_ok(pdir, rel, rec) for rel, rec in files.items())


ACCEPTED_STATES = ("ACCEPTED", "ACCEPTED_BY_HUMAN", "ACCEPTED_BY_POLICY")


def accepted(cycle):
    return cycle["status"] in ACCEPTED_STATES


def latest_cycle(st, target, cset):
    cycles = [r for r in st["reviews"] if r["target"] == target and r["set"] == cset]
    return cycles[-1] if cycles else None


def gate_review_problems(pdir, st, gate):
    problems = []
    for cset in criteria()["gate_reviews"].get(gate, []):
        cyc = latest_cycle(st, "gate:" + gate, cset)
        if cyc is None:
            problems.append("REVIEW_REQUIRED: %s needs an independent '%s' review accepted against the current files "
                            "(`review open --target gate:%s --set %s --creator <agent>`)" % (gate, cset, gate, cset))
        elif not accepted(cyc):
            problems.append("REVIEW_NOT_ACCEPTED: %s review %s is %s" % (cset, cyc["id"], cyc["status"]))
        elif not files_unchanged(pdir, cyc["files"]):
            problems.append("REVIEW_STALE: files changed after review %s was accepted; re-review required" % cyc["id"])
    return problems


def cmd_review(args):
    args.id = args.id or args.rid
    pdir = C.project_dir(args)
    st = C.load(pdir)
    act = args.action
    if act == "criteria":
        for c in criteria_set(args.set):
            print("%s%s  %s" % (c["id"], " [media: direct evidence required]" if c["media"] else "", c["text"]))
        return 0
    if act == "list":
        for r in st["reviews"]:
            print("%s  %-24s %-20s %-10s creator=%s reviewer=%s round=%d" % (
                r["id"], r["target"], r["status"], r["set"], r["creator"], r.get("reviewer") or "-", r["round"]))
        return 0
    if act == "open":
        cset = args.set
        crit = criteria_set(cset)
        if not args.target or not args.creator:
            raise C.StudioError("review open needs --target and --creator")
        kind, _, val = args.target.partition(":")
        if kind == "gate":
            val = val.upper()
            C.gate_def(val)
            if cset not in criteria()["gate_reviews"].get(val, []):
                raise C.StudioError("set %s is not a required review for %s" % (cset, val))
            rels = C.expand(pdir, C.gate_def(val)[3])
            if not rels:
                raise C.StudioError("no deliverables exist yet for %s" % val)
        else:
            rels = [f for f in (args.files or "").split(",") if f]
            if not rels:
                raise C.StudioError("--files required for %s targets" % kind)
        for rel in rels:
            if not (pdir / rel).exists():
                raise C.StudioError("file not found: %s" % rel)
        target = "%s:%s" % (kind, val)
        prior = [r for r in st["reviews"] if r["target"] == target and r["set"] == cset]
        rid = C.next_id("RC", [r["id"] for r in st["reviews"]], 4)
        st["reviews"].append({"id": rid, "target": target, "set": cset, "creator": args.creator, "round": len(prior) + 1,
                              "files": {r: C.fingerprint(pdir, r) for r in rels}, "status": "OPEN",
                              "opened_at": C.now(), "reviewer": None, "results": {}, "summary": "", "human_ack": None})
        C.save(pdir, st)
        print("%s opened (round %d). Criteria:" % (rid, len(prior) + 1))
        for c in crit:
            print("  %s%s  %s" % (c["id"], " [media]" if c["media"] else "", c["text"]))
        return 0
    if act == "pick":
        return review_pick(args, pdir, st)
    cyc = next((r for r in st["reviews"] if r["id"] == args.id), None)
    if not cyc:
        raise C.StudioError("unknown review cycle %s" % args.id)
    if act == "close":
        if cyc["status"] != "OPEN":
            raise C.StudioError("cycle %s is %s, not OPEN" % (cyc["id"], cyc["status"]))
        if not args.reviewer or not args.verdict_file:
            raise C.StudioError("review close needs --reviewer and --verdict-file")
        same = args.reviewer.strip().lower() == cyc["creator"].strip().lower()
        if same and not args.same_actor_reason:
            raise C.StudioError("NOT_INDEPENDENT: reviewer must differ from creator %r (or give --same-actor-reason; "
                                "the result then always needs human review)" % cyc["creator"])
        if not files_unchanged(pdir, cyc["files"]):
            cyc["status"] = "VOID"
            C.save(pdir, st)
            raise C.StudioError("VOID: deliverable changed while under review; open a new cycle")
        verdict = json.loads(Path(args.verdict_file).read_text(encoding="utf-8"))
        res = verdict.get("criteria", {})
        crit = {c["id"]: c for c in criteria_set(cyc["set"])}
        errors = []
        for cid, c in crit.items():
            r = res.get(cid)
            if not r:
                errors.append("missing criterion %s" % cid)
                continue
            if r.get("result") not in ("PASS", "FAIL", "NOT_VERIFIED"):
                errors.append("%s: result must be PASS/FAIL/NOT_VERIFIED" % cid)
                continue
            ev = (r.get("evidence") or "").strip()
            if len(ev) < 20:
                errors.append("%s: concrete evidence (>=20 chars) required for every result" % cid)
            efiles = r.get("evidence_files", [])
            for ef in efiles:
                if not (pdir / ef).exists():
                    errors.append("%s: evidence file not found: %s" % (cid, ef))
            if r.get("result") == "PASS" and c["media"] and not efiles:
                errors.append("%s: media criterion cannot PASS without evidence_files from direct inspection "
                              "(use NOT_VERIFIED to request human review)" % cid)
        extra = set(res) - set(crit)
        if extra:
            errors.append("unknown criteria: %s" % ", ".join(sorted(extra)))
        if errors:
            raise C.StudioError("INVALID_VERDICT:\n  " + "\n  ".join(errors))
        results = {k: res[k] for k in crit}
        vals = [r["result"] for r in results.values()]
        if "FAIL" in vals:
            status = "REVISION_NEEDED"
        elif "NOT_VERIFIED" in vals or same:
            status = "NEEDS_HUMAN_REVIEW"
        else:
            status = "ACCEPTED"
        cyc.update(status=status, reviewer=args.reviewer, results=results, summary=verdict.get("summary", ""),
                   findings=verdict.get("findings", []), closed_at=C.now(), same_actor_reason=args.same_actor_reason)
        (pdir / "reviews" / "cycles").mkdir(parents=True, exist_ok=True)
        (pdir / "reviews" / "cycles" / ("%s.json" % cyc["id"])).write_text(json.dumps(cyc, indent=2), encoding="utf-8")
        C.log_gate(pdir, "REVIEW", "%s %s (%s, target %s)" % (cyc["id"], status, cyc["set"], cyc["target"]),
                   args.reviewer, cyc["summary"])
        C.save(pdir, st)
        print("%s -> %s" % (cyc["id"], status))
        if status == "REVISION_NEEDED":
            for cid, r in results.items():
                if r["result"] == "FAIL":
                    print("  FAIL %s: %s" % (cid, r["evidence"]))
        if status == "NEEDS_HUMAN_REVIEW":
            print("  Human inspection required (unverified criteria or non-independent review).")
        return 0
    if act == "human-ack":
        override = cyc["status"] == "REVISION_NEEDED"
        if cyc["status"] != "NEEDS_HUMAN_REVIEW" and not override:
            raise C.StudioError("only NEEDS_HUMAN_REVIEW (or, as an explicit producer override, REVISION_NEEDED) cycles take "
                                "a human acknowledgement (now %s)" % cyc["status"])
        if not args.by or not args.note:
            raise C.StudioError("--by <human> and --note (what you inspected) are required")
        if override and "override" not in args.note.lower():
            raise C.StudioError("overriding a REVISION_NEEDED verdict needs the word 'override' and the reason in --note")
        if not files_unchanged(pdir, cyc["files"]):
            raise C.StudioError("files changed since review; open a new cycle")
        cyc["status"], cyc["human_ack"] = "ACCEPTED_BY_HUMAN", {"by": args.by, "note": args.note, "at": C.now(),
                                                               "override_of": "REVISION_NEEDED" if override else None}
        C.log_gate(pdir, "REVIEW", "%s ACCEPTED_BY_HUMAN" % cyc["id"], args.by, args.note)
        C.save(pdir, st)
        print("%s -> ACCEPTED_BY_HUMAN" % cyc["id"])
        return 0
    if act == "human-reject":
        if cyc["status"] != "NEEDS_HUMAN_REVIEW":
            raise C.StudioError("only NEEDS_HUMAN_REVIEW cycles take a human rejection (now %s)" % cyc["status"])
        if not args.by or not args.note:
            raise C.StudioError("--by <human> and --note (what failed on inspection) are required")
        cyc.setdefault("findings", []).append({"id": "HV-%03d" % (len(cyc.get("findings", [])) + 1), "severity": "MAJOR",
                                               "where": "human inspection", "issue": args.note,
                                               "required_fix": "Revise the prompt to fix this and regenerate."})
        cyc["status"], cyc["human_ack"] = "REVISION_NEEDED", {"by": args.by, "note": args.note, "at": C.now(), "rejected": True}
        C.log_gate(pdir, "REVIEW", "%s REJECTED_BY_HUMAN" % cyc["id"], args.by, args.note)
        C.save(pdir, st)
        print("%s -> REVISION_NEEDED (human)" % cyc["id"])
        return 0
    raise C.StudioError("unknown review action")


def review_pick(args, pdir, st):
    """Best-of-N acceptance by an independent reviewer, allowed only when a human enabled review_loop.pick_best.
    The candidates are outputs that already had a closed independent review; the chosen one must carry no BLOCKER."""
    pol_p = Path(pdir) / "approval_policy.json"
    pol = json.loads(pol_p.read_text(encoding="utf-8")) if pol_p.exists() else {}
    rl = pol.get("review_loop", {})
    if not rl.get("pick_best"):
        raise C.StudioError("PICK_NOT_ENABLED: a human must enable it (`policy pick-best --enable --by <human>`)")
    if not args.target or not args.set or not args.reviewer or not args.verdict_file:
        raise C.StudioError("review pick needs --target --set --reviewer --verdict-file")
    if not args.target.startswith(("keyframe:", "shot:")):
        raise C.StudioError("pick is only for media items (keyframe:/shot:)")
    verdict = json.loads(Path(args.verdict_file).read_text(encoding="utf-8"))
    if verdict.get("escalate"):
        raise C.StudioError("ESCALATED: the reviewer flagged a real problem; a human decides (%s)" % verdict.get("reason", ""))
    choice = verdict.get("choice") or ""
    reason = (verdict.get("reason") or "").strip()
    if len(reason) < 20:
        raise C.StudioError("verdict needs a concrete reason (>=20 chars)")
    cands = [r for r in st["reviews"] if r["target"] == args.target and r["set"] == args.set
             and r["status"] in ("REVISION_NEEDED", "NEEDS_HUMAN_REVIEW") and choice in r["files"]]
    if not cands:
        raise C.StudioError("NOT_A_CANDIDATE: %s has no closed independent review on %s" % (choice, args.target))
    src = cands[-1]
    if args.reviewer.strip().lower() == src["creator"].strip().lower():
        raise C.StudioError("NOT_INDEPENDENT: reviewer must differ from creator")
    obsolete = {o.get("id") for o in verdict.get("obsolete_blockers", [])
                if "DEC-007" in (o.get("why") or "")}
    if obsolete and "DEC-007" not in ((Path(pdir) / "PRODUCER_DIRECTIVES.md").read_text(encoding="utf-8")
                                      if (Path(pdir) / "PRODUCER_DIRECTIVES.md").exists() else ""):
        obsolete = set()
    live = [f for f in src.get("findings", []) if f.get("severity") == "BLOCKER" and f.get("id") not in obsolete]
    if live:
        raise C.StudioError("BLOCKER_PRESENT: %s has a BLOCKER finding in %s; a human decides" % (choice, src["id"]))
    for ef in verdict.get("evidence_files", []):
        if not (Path(pdir) / ef).exists():
            raise C.StudioError("evidence file not found: %s" % ef)
    if not (Path(pdir) / choice).exists() or not C.lock_ok(pdir, choice, src["files"][choice]):
        raise C.StudioError("file changed since its review; cannot pick")
    rid = C.next_id("RC", [r["id"] for r in st["reviews"]], 4)
    prior = [r for r in st["reviews"] if r["target"] == args.target and r["set"] == args.set]
    cyc = {"id": rid, "target": args.target, "set": args.set, "creator": src["creator"], "round": len(prior) + 1,
           "files": {choice: src["files"][choice]}, "status": "ACCEPTED_BY_POLICY", "opened_at": C.now(),
           "closed_at": C.now(), "reviewer": args.reviewer, "results": src.get("results", {}),
           "summary": "best-of-N pick: " + reason, "findings": src.get("findings", []), "human_ack": None,
           "pick": {"from_cycle": src["id"], "candidates": verdict.get("candidates", []),
                    "policy": "review_loop.pick_best by %s" % rl.get("pick_best_by", "?"),
                    "evidence_files": verdict.get("evidence_files", [])}}
    st["reviews"].append(cyc)
    (Path(pdir) / "reviews" / "cycles").mkdir(parents=True, exist_ok=True)
    (Path(pdir) / "reviews" / "cycles" / ("%s.json" % rid)).write_text(json.dumps(cyc, indent=2), encoding="utf-8")
    C.log_gate(pdir, "REVIEW", "%s ACCEPTED_BY_POLICY (best-of-N pick of %s for %s)" % (rid, choice, args.target),
               args.reviewer, reason)
    C.save(pdir, st)
    print("%s -> ACCEPTED_BY_POLICY (%s)" % (rid, choice))
    return 0


# ------------------------------------------------------------------ shots
def read_shots(pdir):
    return C.read_csv(pdir / "SHOT_LIST.csv")


def write_shots(pdir, rows):
    cols = C.CSV_HEADERS["SHOT_LIST.csv"].split(",")
    with open(pdir / "SHOT_LIST.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def shot_review_ok(pdir, st, shot_id):
    cyc = latest_cycle(st, "shot:" + shot_id, "shot_media")
    return bool(cyc and accepted(cyc) and files_unchanged(pdir, cyc["files"]))


def cmd_shot(args):
    pdir = C.project_dir(args)
    st = C.load(pdir)
    rows = read_shots(pdir)
    row = next((r for r in rows if r["shot_id"] == args.id), None)
    if not row:
        raise C.StudioError("unknown shot %s" % args.id)
    if args.status not in C.SHOT_STATES:
        raise C.StudioError("status must be one of %s" % C.SHOT_STATES)
    if args.status == "APPROVED" and not shot_review_ok(pdir, st, args.id):
        raise C.StudioError("REVIEW_REQUIRED: shot %s cannot be APPROVED without an independent accepted 'shot_media' "
                            "review of the current output file (not on the producer's word)" % args.id)
    row["status"] = args.status
    write_shots(pdir, rows)
    print("%s -> %s" % (args.id, args.status))
    return 0


# ------------------------------------------------------------------ attempts / generation
def gen_rows(pdir):
    return C.read_csv(pdir / "GENERATION_LOG.csv")


def consecutive_failures(rows, shot, capability):
    n = 0
    for r in reversed([x for x in rows if x["shot_id"] == shot and x["capability"] == capability]):
        if r["outcome"] == "FAILED":
            n += 1
        else:
            break
    return n


def log_attempt(pdir, st, rec):
    rows = gen_rows(pdir)
    rec["attempt_id"] = C.next_id("GA", [r["attempt_id"] for r in rows], 4)
    rec["timestamp"] = C.now()
    if rec["outcome"] == "FAILED" and not (rec.get("failure_category") and (rec.get("error_analysis") or "").strip()):
        raise C.StudioError("FAILED attempts need --failure-category and --analysis (error analysis drives the next try)")
    cost = float(rec.get("cost_usd") or 0)
    if cost > 0:
        spend(st, rec.get("authorization"), cost)
        crow = C.read_csv(pdir / "COST_TRACKER.csv")
        C.append_csv(pdir / "COST_TRACKER.csv", C.CSV_HEADERS["COST_TRACKER.csv"], {
            "entry_id": C.next_id("CE", [r["entry_id"] for r in crow], 4), "date": C.today(), "kind": "ACTUAL",
            "stage": rec.get("phase", ""), "item": "%s %s %s (%s)" % (rec["capability"], rec["shot_id"], rec["outcome"], rec["attempt_id"]),
            "units": 1, "unit_cost": cost, "amount": cost, "currency": st["budget"]["currency"],
            "authorized_by": rec["authorization"], "notes": rec.get("kind", "GENERATION")})
    if rec.get("output_path"):
        op = pdir / rec["output_path"]
        if not op.exists():
            raise C.StudioError("output_path does not exist: %s (never record outputs that were not produced)" % rec["output_path"])
        rec["output_sha256"] = C.sha256_file(op)
    C.append_csv(pdir / "GENERATION_LOG.csv", C.CSV_HEADERS["GENERATION_LOG.csv"], rec)
    shots = read_shots(pdir)
    row = next((r for r in shots if r["shot_id"] == rec["shot_id"]), None)
    if row and rec["outcome"] == "FAILED":
        row["retries"] = str(int(row["retries"] or 0) + 1)
        write_shots(pdir, shots)
    return rec["attempt_id"]


def spend(st, auth, amount):
    return C.spend(st, auth, amount)


def cmd_attempt(args):
    pdir = C.project_dir(args)
    st = C.load(pdir)
    if args.action == "list":
        for r in gen_rows(pdir):
            print("%s %s %-14s %-9s %s/%s cost=%s %s" % (r["attempt_id"], r["shot_id"], r["capability"], r["outcome"],
                                                      r["provider"], r["model"], r["cost_usd"] or 0, r["failure_category"]))
        return 0
    if args.outcome not in ("SUCCESS", "FAILED"):
        raise C.StudioError("--outcome SUCCESS|FAILED (record what really happened)")
    rows = gen_rows(pdir)
    n = consecutive_failures(rows, args.shot, args.capability)
    need = st["adaptive_retry"]["strategy_change_after_consecutive_failures"]
    if n >= need and not args.strategy_change:
        raise C.StudioError("STRATEGY_CHANGE_REQUIRED: %d consecutive failures on %s/%s; describe what changes "
                            "(prompt, reference, model, provider, simplified action) via --strategy-change" % (n, args.shot, args.capability))
    cap = st["adaptive_retry"].get("hard_cap")
    if cap and n >= cap:
        raise C.StudioError("HARD_CAP: configured hard cap of %s consecutive failures reached" % cap)
    aid = log_attempt(pdir, st, {
        "shot_id": args.shot, "capability": args.capability, "phase": args.phase or "", "provider": args.provider,
        "model": args.model or "unspecified", "kind": "REVISION" if args.revision else "GENERATION", "outcome": args.outcome,
        "failure_category": args.failure_category or "", "error_analysis": args.analysis or "",
        "strategy_change": args.strategy_change or "", "cost_usd": args.cost or "0", "authorization": args.authorization or "",
        "output_path": args.output or "", "synthetic": "yes" if args.synthetic else "no", "notes": args.notes or ""})
    C.save(pdir, st)
    print(aid)
    return 0


PHASE_GATE = {"design": "GATE-03", "proof": "GATE-05", "production": "GATE-07"}


def cmd_generate(args):
    pdir = C.project_dir(args)
    st = C.load(pdir)
    cfg = providers.load_config(pdir)
    name, prov, model = providers.resolve(cfg, args.capability)
    model = args.model or model
    need_gate = PHASE_GATE[args.phase]
    if st["gates"][need_gate]["state"] != "APPROVED":
        raise C.StudioError("GATE_REQUIRED: phase '%s' needs %s APPROVED" % (args.phase, need_gate))
    shot = args.shot
    autonomy.hold_check(pdir, shot)  # e.g. CA-001: tagged shots are never generated before a human release
    kind = providers.cap_kind(args.capability)
    input_rel = getattr(args, "input", None)
    if providers.needs_input(args.capability):
        if not input_rel or not (pdir / input_rel).exists():
            raise C.StudioError("INPUT_REQUIRED: %s needs --input <approved keyframe inside the project>" % args.capability)
    rows = gen_rows(pdir)
    n = consecutive_failures(rows, shot, args.capability)
    if n >= st["adaptive_retry"]["strategy_change_after_consecutive_failures"] and not args.strategy_change:
        raise C.StudioError("STRATEGY_CHANGE_REQUIRED after %d consecutive failures: review the error analysis and state "
                            "what you change (--strategy-change)" % n)
    prompt_file = pdir / (args.prompt_file or providers.default_prompt(args.capability, shot))
    if not prompt_file.exists():
        raise C.StudioError("prompt file missing: %s (write the production-ready prompt first)" % prompt_file.relative_to(pdir))
    status, detail = providers.availability(prov)
    est = providers.estimate(prov, args.capability, args.units)
    if est is None and args.estimated_cost is not None:
        est = float(args.estimated_cost)
    print("provider=%s model=%s availability=%s (%s)" % (name, model, status, detail))
    if status in ("MISSING_LOCAL_TOOL", "MISSING_CREDENTIAL_ENV"):
        raise C.StudioError("%s: %s -> mark stage BLOCKED_EXTERNAL_TOOL" % (status, detail))
    if prov["type"] == "prompt-file":
        print("BLOCKED_EXTERNAL_TOOL: no generation provider routed for %s. Prompt file is ready: %s" % (
            args.capability, prompt_file.relative_to(pdir)))
        return 3
    if est is None:
        raise C.StudioError("PRICE_UNKNOWN: no pricing for %s on %s; take the real price from the tool and pass "
                            "--estimated-cost (never guess)" % (args.capability, name))
    print("estimated cost: %.4f %s" % (est, st["budget"]["currency"]))
    if est > 0:
        a = next((x for x in st["authorizations"] if x["id"] == args.authorization), None)
        if not a:
            raise C.StudioError("AUTHORIZATION_REQUIRED: obtain explicit human cost authorization first "
                                "(`cost authorize`), then pass --authorization AU-xxxx")
        if a["consumed"] + est > a["amount"]:
            raise C.StudioError("AUTHORIZATION_EXCEEDED: %.2f left, need %.2f" % (a["amount"] - a["consumed"], est))
    if prov["type"] == "connector":
        if not args.connector_verified:
            raise C.StudioError("CONNECTOR_UNVERIFIED: confirm the tool is connected in this session (--connector-verified)")
        print("PREFLIGHT_OK. Execute the connected tool with %s, then run `attempt record` with the real outcome/cost."
              % prompt_file.relative_to(pdir))
        return 0
    ext_ = providers.KIND_EXT[kind]
    tag = providers.CAP_TAG.get(args.capability, "")
    sub = providers.KIND_DIR[kind]
    vers = len(list((pdir / "media" / sub / shot).glob("%s%s_v*%s" % (shot, tag, ext_)))) + 1
    out_rel = "media/%s/%s/%s%s_v%02d%s" % (sub, shot, shot, tag, vers, ext_)
    result = providers.execute(prov, args.capability, {"prompt_file": prompt_file, "output": pdir / out_rel,
                                                       "units": args.units, "shot_id": shot,
                                                       "input": str(pdir / input_rel) if input_rel else ""})
    ok = result["status"] == "SUCCESS"
    aid = log_attempt(pdir, st, {
        "shot_id": shot, "capability": args.capability, "phase": args.phase, "provider": name, "model": model,
        "kind": "REVISION" if args.revision else "GENERATION", "outcome": "SUCCESS" if ok else "FAILED",
        "failure_category": "" if ok else (args.failure_category or "provider_error"),
        "error_analysis": "" if ok else (result["detail"] or "provider returned no output"),
        "strategy_change": args.strategy_change or "", "cost_usd": est if est else "0",
        "authorization": args.authorization or "", "output_path": out_rel if ok else "",
        "synthetic": "yes" if result.get("synthetic") else "no", "notes": result["detail"][:200]})
    C.save(pdir, st)
    print("%s %s %s" % (aid, result["status"], out_rel if ok else ""))
    return 0 if ok else 1


# ------------------------------------------------------------------ inspection
def cmd_inspect(args):
    pdir = C.project_dir(args)
    f = (pdir / args.file) if not os.path.isabs(args.file) else Path(args.file)
    if not f.exists():
        raise C.StudioError("file not found: %s" % args.file)
    if not shutil.which("ffmpeg") or not C.has_ffprobe():
        raise C.StudioError("INSPECTION_UNAVAILABLE: ffmpeg/ffprobe missing. Report criteria as NOT_VERIFIED and request "
                            "human review.")
    info = C.probe(f)
    out_dir = pdir / "reviews" / "evidence" / ("%s_%s" % (f.stem, C.sha256_file(f)[:8]))
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    has_video = any(s["type"] == "video" for s in info["streams"])
    if has_video:
        if f.suffix.lower() in (".png", ".jpg", ".jpeg"):
            targets = [0.0]
        else:
            targets = [info["duration_s"] * (i + 0.5) / args.frames for i in range(args.frames)]
        for i, ts in enumerate(targets, 1):
            fp = out_dir / ("frame_%02d.jpg" % i)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "%.3f" % ts, "-i", str(f), "-frames:v", "1", "-q:v", "2", str(fp)],
                           capture_output=True)
            if fp.exists():
                frames.append(os.path.relpath(fp, pdir))
    audio = None
    if any(s["type"] == "audio" for s in info["streams"]):
        r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(f), "-af", "volumedetect", "-vn", "-f", "null", "-"],
                           capture_output=True, text=True)
        mean = re.search(r"mean_volume:\s*(-?[\d.]+) dB", r.stderr)
        mx = re.search(r"max_volume:\s*(-?[\d.]+) dB", r.stderr)
        audio = {"mean_volume_db": float(mean.group(1)) if mean else None, "max_volume_db": float(mx.group(1)) if mx else None}
        vd = [s for s in info["streams"] if s["type"] == "video"]
        if vd:
            d = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "json", str(f)],
                               capture_output=True, text=True)
            try:
                durs = {s["codec_type"]: float(s["duration"]) for s in json.loads(d.stdout)["streams"] if "duration" in s}
                if "audio" in durs and "video" in durs:
                    audio["av_duration_offset_s"] = round(durs["audio"] - durs["video"], 3)
            except (ValueError, KeyError):
                pass
    report = {"file": os.path.relpath(f, pdir) if str(f).startswith(str(pdir)) else str(f),
              "sha256": C.sha256_file(f), "probe": info, "frames": frames, "audio": audio,
              "note": "Frames are evidence for a reviewer to LOOK at (Read the jpgs). This command does not judge quality."}
    (out_dir / "inspection.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print("evidence dir: %s" % os.path.relpath(out_dir, pdir))
    return 0


def cmd_techcheck(args):
    pdir = C.project_dir(args)
    st = C.load(pdir)
    spec = st.get("delivery_spec")
    if not spec:
        raise C.StudioError("delivery_spec not set in PROJECT_STATUS.json (open decision Q-005): ask the human for the spec")
    f = (pdir / args.file) if not os.path.isabs(args.file) else Path(args.file)
    info = C.probe(f)
    v = next((s for s in info["streams"] if s["type"] == "video"), {})
    a = next((s for s in info["streams"] if s["type"] == "audio"), {})
    fps = None
    if v.get("fps") and "/" in str(v["fps"]):
        num, den = str(v["fps"]).split("/")
        fps = float(num) / float(den) if float(den) else None
    checks = []

    def chk(name, expected, actual, ok):
        checks.append({"property": name, "expected": expected, "actual": actual, "result": "PASS" if ok else "FAIL"})
    if "width" in spec:
        chk("width", spec["width"], v.get("width"), v.get("width") == spec["width"])
    if "height" in spec:
        chk("height", spec["height"], v.get("height"), v.get("height") == spec["height"])
    if "fps" in spec:
        chk("fps", spec["fps"], fps, fps is not None and abs(fps - spec["fps"]) < 0.05)
    if "video_codec" in spec:
        chk("video_codec", spec["video_codec"], v.get("codec"), v.get("codec") == spec["video_codec"])
    if "audio_codec" in spec:
        chk("audio_codec", spec["audio_codec"], a.get("codec"), a.get("codec") == spec["audio_codec"])
    if "duration_s" in spec:
        tol = spec["duration_s"] * spec.get("duration_tolerance_pct", 5) / 100.0
        chk("duration_s", spec["duration_s"], info["duration_s"], abs(info["duration_s"] - spec["duration_s"]) <= tol)
    out = pdir / "reviews" / "evidence"
    out.mkdir(parents=True, exist_ok=True)
    ev = out / ("techcheck_%s_%s.json" % (f.stem, C.sha256_file(f)[:8]))
    ev.write_text(json.dumps({"file": str(f.name), "checks": checks}, indent=2), encoding="utf-8")
    for c in checks:
        print("%-4s %-12s expected=%s actual=%s" % (c["result"], c["property"], c["expected"], c["actual"]))
    print("evidence: %s" % os.path.relpath(ev, pdir))
    return 1 if any(c["result"] == "FAIL" for c in checks) else 0


# ------------------------------------------------------------------ economics
def cmd_report(args):
    pdir = C.project_dir(args)
    st = C.load(pdir)
    rows = gen_rows(pdir)
    b = st["budget"]
    by = {}
    failed_cost = revision_cost = 0.0
    for r in rows:
        k = (r["provider"], r["model"])
        d = by.setdefault(k, {"attempts": 0, "success": 0, "failed": 0, "cost": 0.0})
        d["attempts"] += 1
        d["success" if r["outcome"] == "SUCCESS" else "failed"] += 1
        c = float(r["cost_usd"] or 0)
        d["cost"] += c
        if r["outcome"] == "FAILED":
            failed_cost += c
        if r["kind"] == "REVISION":
            revision_cost += c
    est, act = C.cost_totals(pdir)
    auth = sum(a["amount"] for a in st["authorizations"])
    consumed = sum(a["consumed"] for a in st["authorizations"])
    shots = read_shots(pdir)
    open_shots = [s for s in shots if s["status"] != "APPROVED"]
    approved = [s for s in shots if s["status"] == "APPROVED"]
    print("== تقرير اقتصاديات الإنتاج: %s ==" % st["project_id"])
    print("استخدام النماذج والمزوّدين:")
    for (prov, model), d in sorted(by.items()):
        print("  %-18s %-22s محاولات=%d ناجحة=%d فاشلة=%d التكلفة=%.2f" % (prov, model, d["attempts"], d["success"], d["failed"], d["cost"]))
    if not by:
        print("  (لا توجد محاولات توليد مسجلة بعد — لا شيء صُرف)")
    print("إجمالي المحاولات: %d | الفاشلة: %d | تكلفة الفاشلة: %.2f | تكلفة المراجعات (Revisions): %.2f" % (
        len(rows), sum(1 for r in rows if r["outcome"] == "FAILED"), failed_cost, revision_cost))
    print("التكلفة الفعلية: %.2f | المصرَّح بها: %.2f (المستهلك %.2f، المتبقي %.2f) | الميزانية المعتمدة: %s %s" % (
        act, auth, consumed, auth - consumed, b["approved_total"], b["currency"]))
    if approved:
        avg = act / len(approved)
        print("المتبقي المقدّر للإنتاج: ≈ %.2f (%d لقطة متبقية × متوسط فعلي %.2f للقطة المعتمدة)" % (avg * len(open_shots), len(open_shots), avg))
    else:
        print("المتبقي المقدّر للإنتاج: غير معروف بعد — لا توجد لقطات معتمدة لحساب متوسط فعلي؛ أسعار المزوّد %s." % (
            "غير مضبوطة" if b["rates"]["video_second"] is None else "مضبوطة"))
    print("تقدير مسجّل (ESTIMATE): %.2f — منفصل عن الفعلي." % est)
    return 0


# ------------------------------------------------------------------ upgrade / models / teams / hook
def cmd_upgrade(args):
    pdir = C.project_dir(args)
    st = C.load(pdir)
    changed = []
    for rel in C.EXTRA_FILES + C.ROOT_FILES:
        if not (pdir / rel).exists() and (C.TEMPLATES / C.TEMPLATE_MAP.get(rel, rel)).exists():
            text = (C.TEMPLATES / C.TEMPLATE_MAP.get(rel, rel)).read_text(encoding="utf-8").replace("{{PROJECT_ID}}", st["project_id"])
            (pdir / rel).parent.mkdir(parents=True, exist_ok=True)
            (pdir / rel).write_text(text, encoding="utf-8")
            changed.append("added " + rel)
    for k, v in (("authorizations", []), ("reviews", []), ("delivery_spec", None),
                 ("adaptive_retry", {"strategy_change_after_consecutive_failures": 2, "hard_cap": None})):
        if k not in st:
            st[k] = v
            changed.append("added key " + k)
    if st.pop("limits", None) is not None:
        changed.append("removed fixed retry cap (replaced by adaptive retry with error analysis)")
    for name, rec in st["stages"].items():
        owner = C.STAGE_BY[name][4]
        if rec["owner"] != owner:
            rec["owner"] = owner
            changed.append("stage %s owner -> %s" % (name, owner))
    if st.get("schema_version") != C.SCHEMA_VERSION:
        changed.append("schema %s -> %s" % (st.get("schema_version"), C.SCHEMA_VERSION))
        st["schema_version"] = C.SCHEMA_VERSION
    for d in ("reviews/evidence", "reviews/cycles"):
        (pdir / d).mkdir(parents=True, exist_ok=True)
    if changed:
        C.save(pdir, st)
        C.append_text(pdir / "CHANGE_LOG.md", "## %s — framework upgrade\n- By: studio.py upgrade\n- Summary: %s\n\n" % (C.now(), "; ".join(changed)))
    print("\n".join(changed) or "already up to date")
    return 0


def agent_files():
    return sorted((C.STUDIO_ROOT / ".claude" / "agents").glob("*.md"))


def read_model(path):
    m = re.search(r"^model:\s*(\S+)\s*$", path.read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else None


def cmd_models(args):
    pol = json.loads((C.STUDIO_ROOT / "standards" / "model_policy.json").read_text(encoding="utf-8"))
    target = args.model or pol["strongest"]
    bad = []
    for f in agent_files():
        want = pol["roles"].get(f.stem, "strongest")
        want = target if want == "strongest" else want
        have = read_model(f)
        if have != want:
            bad.append((f, have, want))
            if args.action == "apply":
                txt = f.read_text(encoding="utf-8")
                txt = re.sub(r"^model:.*$", "model: %s" % want, txt, count=1, flags=re.M)
                f.write_text(txt, encoding="utf-8")
    if args.action == "apply":
        if args.model:
            pol["strongest"] = args.model
            (C.STUDIO_ROOT / "standards" / "model_policy.json").write_text(json.dumps(pol, indent=2), encoding="utf-8")
        print("updated %d agents to follow the policy (strongest=%s)" % (len(bad), target))
        return 0
    for f, have, want in bad:
        print("MISMATCH %s: has %s, policy wants %s" % (f.stem, have, want))
    print("model policy OK (strongest=%s)" % target if not bad else "model policy VIOLATED")
    return 1 if bad else 0


def teams_enabled():
    if os.environ.get("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS") == "1":
        return True, "shell env"
    for p in (C.STUDIO_ROOT / ".claude" / "settings.json", C.STUDIO_ROOT / ".claude" / "settings.local.json",
              Path.home() / ".claude" / "settings.json"):
        try:
            if json.loads(p.read_text()).get("env", {}).get("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS") == "1":
                return True, str(p)
        except (OSError, ValueError):
            pass
    return False, "not enabled (experimental feature is off by default)"


def teams_problems():
    reg = json.loads((C.STUDIO_ROOT / "standards" / "teams.json").read_text(encoding="utf-8"))
    agents = {f.stem for f in agent_files()}
    skills = {p.parent.name for p in (C.STUDIO_ROOT / ".claude" / "skills").glob("*/SKILL.md")}
    problems = []
    for tid, t in reg["teams"].items():
        for k in ("mission", "mode", "creators", "reviewers", "inputs", "outputs", "depends_on", "review_sets", "acceptance", "fallback", "spawn_prompt"):
            if k not in t:
                problems.append("%s: missing %s" % (tid, k))
        if t.get("mode") not in ("agent_team", "subagents", "hybrid"):
            problems.append("%s: bad mode" % tid)
        for a in t.get("creators", []) + t.get("reviewers", []):
            if a not in agents:
                problems.append("%s: unknown agent %s" % (tid, a))
        if set(t.get("creators", [])) & set(t.get("reviewers", [])):
            problems.append("%s: creator also reviewer (%s)" % (tid, ", ".join(set(t["creators"]) & set(t["reviewers"]))))
        if not t.get("reviewers"):
            problems.append("%s: no independent reviewer" % tid)
        for s in t.get("review_sets", []):
            if s not in criteria()["sets"]:
                problems.append("%s: unknown review set %s" % (tid, s))
        for d in t.get("depends_on", []):
            if d not in reg["teams"]:
                problems.append("%s: unknown dependency %s" % (tid, d))
        if t.get("mode") in ("agent_team", "hybrid") and "subagents" not in t.get("fallback", "").lower():
            problems.append("%s: agent-team mode needs a subagent fallback" % tid)
    return reg, problems


def cmd_teams(args):
    reg, problems = teams_problems()
    en, where = teams_enabled()
    if args.action == "show":
        for tid, t in reg["teams"].items():
            print("%-26s %-9s creators=%s reviewers=%s deps=%s" % (tid, t["mode"], ",".join(t["creators"]),
                                                                  ",".join(t["reviewers"]), ",".join(t["depends_on"]) or "-"))
        print("Agent Teams (experimental): %s — %s" % ("ENABLED" if en else "DISABLED", where))
        return 0
    for p in problems:
        print("ERROR %s" % p)
    print("Agent Teams (experimental): %s — %s%s" % ("ENABLED" if en else "DISABLED", where,
                                                     "" if en else "; team-mode work falls back to subagents"))
    print("teams registry VALID" if not problems else "teams registry INVALID")
    return 1 if problems else 0


def cmd_hook_guard(args):
    """PreToolUse guard for Write/Edit: protects engine-owned and approved (locked) files."""
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    ti = data.get("tool_input", {}) or {}
    fp = ti.get("file_path") or ti.get("path") or ti.get("notebook_path")
    if not fp:
        return 0
    base = C.projects_dir(args).resolve()
    p = Path(fp)
    if not p.is_absolute():
        p = Path(data.get("cwd") or os.getcwd()) / p
    try:
        rel = p.resolve().relative_to(base)
    except ValueError:
        return 0
    parts = rel.parts
    if len(parts) < 2:
        return 0
    pdir, inner = base / parts[0], "/".join(parts[1:])
    if inner in ("PROJECT_STATUS.json", "APPROVAL_LOG.md", "GENERATION_LOG.csv"):
        print("BLOCKED: %s is written only by studio.py (audit integrity). Use the CLI." % inner, file=sys.stderr)
        return 2
    if inner.startswith("reviews/cycles/") or inner.startswith("versions/"):
        print("BLOCKED: %s is an audit artefact; never edit." % inner, file=sys.stderr)
        return 2
    if inner in ("approval_policy.json", "providers.json") or inner.startswith("orchestrator/"):
        print("BLOCKED: %s is controlled by humans / the orchestrator (approval policy, provider routing and spending "
              "or run state). Use `studio.py policy|film ...`." % inner, file=sys.stderr)
        return 2
    try:
        st = C.load(pdir)
    except (OSError, ValueError):
        return 0
    for gid, g in st["gates"].items():
        if inner in g["locked"] and g["locked"][inner]["mode"] == "full":
            print("BLOCKED: %s is APPROVED and locked in %s. Use `studio.py revise %s` (archives the version) first."
                  % (inner, gid, gid), file=sys.stderr)
            return 2
    return 0


# ------------------------------------------------------------------ validate extension
def validate_ext(pdir, st):
    errors, warns = [], []
    with open(pdir / "GENERATION_LOG.csv", newline="", encoding="utf-8") as f:
        first = next(csv.reader(f), [])
    if ",".join(first) != C.CSV_HEADERS["GENERATION_LOG.csv"]:
        errors.append("CSV_HEADER: GENERATION_LOG.csv header differs from template")
        return errors, warns
    ids = {a["id"] for a in st["authorizations"]}
    for a in st["authorizations"]:
        if a["consumed"] > a["amount"] + 1e-9:
            errors.append("AUTHORIZATION_OVERDRAWN: %s" % a["id"])
    if st["budget"]["approved_total"] is not None and sum(a["amount"] for a in st["authorizations"]) > st["budget"]["approved_total"]:
        errors.append("AUTHORIZATIONS_EXCEED_BUDGET")
    for r in gen_rows(pdir):
        if r["outcome"] == "FAILED" and not (r["failure_category"] and r["error_analysis"].strip()):
            errors.append("FAILED_WITHOUT_ANALYSIS: %s" % r["attempt_id"])
        if float(r["cost_usd"] or 0) > 0 and r["authorization"] not in ids:
            errors.append("UNAUTHORIZED_GENERATION_COST: %s" % r["attempt_id"])
        if r["output_path"] and not (pdir / r["output_path"]).exists():
            errors.append("MISSING_OUTPUT: %s -> %s" % (r["attempt_id"], r["output_path"]))
        if r["synthetic"] == "yes":
            warns.append("SYNTHETIC_OUTPUT: %s is mock test media, never deliverable" % r["attempt_id"])
    for rv in st["reviews"]:
        if rv["status"] in ACCEPTED_STATES:
            if rv["status"] in ("ACCEPTED", "ACCEPTED_BY_POLICY") and rv["reviewer"] and rv["reviewer"].lower() == rv["creator"].lower():
                errors.append("REVIEW_NOT_INDEPENDENT: %s" % rv["id"])
    for gid, g in st["gates"].items():
        if g["state"] == "APPROVED":
            for p in gate_review_problems(pdir, st, gid):
                if not p.startswith("REVIEW_STALE") or not any(m != "full" for m in [v["mode"] for v in g["locked"].values()]):
                    errors.append("APPROVED_WITHOUT_REVIEW: %s: %s" % (gid, p.split(":")[0]))
    for s in read_shots(pdir):
        if s["status"] == "APPROVED" and not shot_review_ok(pdir, st, s["shot_id"]):
            errors.append("SHOT_APPROVED_WITHOUT_REVIEW: %s" % s["shot_id"])
    return errors, warns


# ------------------------------------------------------------------ registration
def register(core, add):
    global C
    C = core
    core.SUBMIT_CHECKS.append(gate_review_problems)
    core.VALIDATE_CHECKS.append(validate_ext)
    sp = add("review", cmd_review)
    sp.add_argument("action", choices=["criteria", "open", "close", "human-ack", "human-reject", "list", "pick"])
    sp.add_argument("rid", nargs="?", help="review cycle id (RC-0001) for close/human-ack")
    for a in ("set", "target", "creator", "files", "id", "reviewer", "verdict-file", "same-actor-reason", "by", "note"):
        sp.add_argument("--" + a)
    sp = add("shot", cmd_shot)
    sp.add_argument("action", choices=["set-status"])
    sp.add_argument("--id", required=True)
    sp.add_argument("--status", required=True)
    sp = add("attempt", cmd_attempt)
    sp.add_argument("action", choices=["record", "list"])
    for a in ("shot", "capability", "phase", "provider", "model", "outcome", "failure-category", "analysis",
              "strategy-change", "cost", "authorization", "output", "notes"):
        sp.add_argument("--" + a)
    sp.add_argument("--revision", action="store_true")
    sp.add_argument("--synthetic", action="store_true")
    sp = add("generate", cmd_generate)
    sp.add_argument("--shot", required=True)
    sp.add_argument("--capability", required=True, choices=providers.GENERATION_CAPS)
    sp.add_argument("--phase", required=True, choices=list(PHASE_GATE))
    sp.add_argument("--units", type=float, default=1)
    for a in ("authorization", "prompt-file", "model", "strategy-change", "failure-category"):
        sp.add_argument("--" + a)
    sp.add_argument("--estimated-cost", type=float)
    sp.add_argument("--connector-verified", action="store_true")
    sp.add_argument("--revision", action="store_true")
    sp.add_argument("--input", help="input asset inside the project (image-to-video keyframe)")
    sp = add("inspect", cmd_inspect)
    sp.add_argument("file")
    sp.add_argument("--frames", type=int, default=6)
    sp = add("techcheck", cmd_techcheck)
    sp.add_argument("file")
    sp = add("report", cmd_report)
    sp.add_argument("what", choices=["economics"])
    add("upgrade", cmd_upgrade)
    sp = add("models", cmd_models)
    sp.add_argument("action", choices=["check", "apply"])
    sp.add_argument("--model")
    sp = add("teams", cmd_teams)
    sp.add_argument("action", choices=["check", "show"])
    add("hook-guard", cmd_hook_guard)

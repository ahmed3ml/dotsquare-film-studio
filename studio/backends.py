"""Agent execution backends for the orchestrator.

- ClaudeBackend: runs ONE specialised subagent per job as a headless Claude Code session
  (`claude -p --agent <name>`), with an explicit tool allow-list (no permission bypass), engine-mutating commands
  denied, a per-job spending cap (--max-budget-usd) and a timeout. The project's PreToolUse hook still guards files.
- MockBackend: deterministic local stand-in used by tests (no network, no model calls); everything it writes is
  labelled MOCK, and its "reviews" are synthetic, so mock results never count as real production readiness.
Both return {"status": OK|FAILED|TRANSIENT, "cost_usd", "summary", "retry_after_s"}.
"""
import glob
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

STUDIO_ROOT = Path(__file__).resolve().parent.parent
SAFE_BASH = ["status", "validate", "review criteria", "inspect", "probe", "techcheck", "review list", "pending", "next"]
DENY_ENGINE = ["approve", "reject", "revise", "submit", "cost", "generate", "external", "hold", "policy", "film", "spec",
               "review open", "review close", "review human-ack", "review human-reject", "review pick", "shot set-status", "stage", "task", "upgrade", "models apply",
               "export", "attempt record"]


def _write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ------------------------------------------------------------------ briefs (used by the real backend; stored per job)
def brief(job, pid):
    k = job["kind"]
    head = ("You are running as an autonomous job of the AI Film Studio orchestrator for project %s (job %s).\n"
            "Studio root is the current directory. First run `python3 studio/studio.py status -p %s`, read CLAUDE.md, "
            "projects/%s/PROJECT.md (hard constraints and Decision register are binding) and the APPROVED documents your "
            "task depends on. If projects/<id>/PRODUCER_DIRECTIVES.md exists, read it FIRST: its directives amend the locked "
            "documents and win where they disagree (judge and write prompts by them). Film content, prompts and dialogue are in ENGLISH. Never fabricate sources, quotes, media, "
            "tool output, costs or verification results; if you cannot do something, say so in your final message. "
            "You cannot approve, submit, spend, generate media or change workflow state; the orchestrator does that.\n\n"
            % (pid, job["key"], pid, pid))
    p = "projects/%s/" % pid
    files = ", ".join(p + f for f in job.get("files", []))
    if k == "intake":
        return head + ("TASK: turn this film brief into the project record. Edit only %sPROJECT.md: fill Working title, Genre, "
                       "Runtime, Protagonist, Setting, Visual style and Hard constraints from the brief; record anything "
                       "ambiguous as an Open decision instead of guessing. Brief:\n\n%s\n" % (p, job["extra"]["brief"]))
    if k == "create":
        extra = ""
        if job.get("stage") == "storyboard":
            extra = ("\nSHOT_LIST.csv must keep the engine header exactly; one row per shot with stable IDs SH-<scene>-<nn>, "
                     "seq, duration_s, shot_type, characters, environment_id, dialogue_ref. For EVERY shot also write "
                     "%sprompts/images/<SHOT>.md (keyframe prompt) and %sprompts/video/<SHOT>.md (motion prompt), and "
                     "%sprompts/audio/<SHOT>.md with the exact English dialogue/narration if the shot has any. Write "
                     "%sprompts/audio/MUSIC_music.md and %sprompts/audio/AMBIENCE_sfx.md for the score and ambience beds.\n"
                     % (p, p, p, p, p))
            for h in job.get("extra", {}).get("holds", []):
                extra += ("CONTENT HOLD %s: every shot that depicts %s MUST contain the tag %s in its notes column "
                          "(generation of those shots stays blocked until a human releases the hold).\n"
                          % (h["id"], h.get("description", ""), h["tag"]))
        return head + "TASK (%s, stage %s): create %s using your skills and the approved inputs.%s" % (
            job["agent"], job["stage"], files, extra)
    if k == "revise":
        return head + ("TASK (%s): revise %s to resolve EVERY finding of independent review %s below. Change only what the "
                       "findings require (list anything else you fix). Keep all IDs stable. The previous version is archived "
                       "by the orchestrator. Do not argue without sources.\n\nFINDINGS:\n%s\n" % (
                           job["agent"], files, job.get("cycle_id"), json.dumps(job.get("findings", []), indent=1)))
    if k == "report":
        return head + ("TASK (%s, independent of the creator): write the report %s about the current deliverables (%s). Inspect "
                       "media directly (`python3 studio/studio.py inspect <file> -p %s`, then Read the extracted frames). "
                       "State what you could NOT verify.\n" % (job["agent"], p + job["extra"]["report"],
                                                               ", ".join(job["extra"].get("about", [])), pid))
    if k == "review":
        return head + ("TASK: you are the INDEPENDENT reviewer (%s) for review cycle %s (target %s, criteria set %s). Run "
                       "`python3 studio/studio.py review criteria --set %s`. Evaluate the deliverables %s against every "
                       "criterion. For media, run `python3 studio/studio.py inspect <file> -p %s` and LOOK at the frames; never "
                       "PASS what you did not inspect (use NOT_VERIFIED). Do NOT modify the deliverables (that voids the cycle).\n"
                       "Write evidence to %s and the verdict JSON to %s exactly in this format:\n"
                       '{"criteria": {"<ID>": {"result": "PASS|FAIL|NOT_VERIFIED", "evidence": "specific, >=20 chars", '
                       '"evidence_files": ["reviews/evidence/..."]}}, "summary": "...", "findings": [{"id": "RV-001", '
                       '"severity": "BLOCKER|MAJOR|MINOR", "where": "...", "issue": "...", "required_fix": "..."}]}\n'
                       "evidence_files paths are relative to projects/%s/. Do not run `review close`.\n" % (
                           job["agent"], job["cycle_id"], job["target"], job["set"], job["set"], files, pid,
                           p + job["extra"]["evidence"], p + job["extra"]["verdict"], pid)) + ("For close detail use `studio/bin/media-crop <file> <x> <y> <w> <h> <scale> projects/%s/reviews/evidence/<name>.png [time_s]` (native-pixel crop, upscaled) and for motion `studio/bin/media-frames <video> <start_s> <dur_s<=3> projects/%s/reviews/evidence/<dir> [fps]` (full-rate frames); run each as ONE standalone command (no cd, &&, pipes) and Read the outputs. Use them instead of returning NOT_VERIFIED for detail or motion.\n" % (pid, pid))
    if k == "pick":
        x = job["extra"]
        return head + ("TASK: you are the INDEPENDENT reviewer (%s). Producer rule: each media item gets one retry, then the "
                       "reviewer picks the best version; only a REAL problem goes back to the producer. Target %s (criteria set "
                       "%s; run `python3 studio/studio.py review criteria --set %s`). Candidates and their earlier independent "
                       "reviews:\n%s\nCompare the candidates directly (inspect each: `python3 studio/studio.py inspect <file> "
                       "-p %s`, Read the frames). " % (job["agent"], job["target"], job["set"], job["set"],
                                                      json.dumps(x["candidates"], indent=1), pid)
                       + "For close detail use `studio/bin/media-crop <file> <x> <y> <w> <h> <scale> projects/%s/reviews/evidence/<name>.png [time_s]` (native-pixel crop, upscaled) and for motion `studio/bin/media-frames <video> <start_s> <dur_s<=3> projects/%s/reviews/evidence/<dir> [fps]` (full-rate frames); run each as ONE standalone command (no cd, &&, pipes) and Read the outputs. Use them instead of returning NOT_VERIFIED for detail or motion.\n" % (pid, pid) +
                       "Pick the best usable one. ESCALATE (escalate=true) ONLY for a real problem: a project-rule violation "
                       "(DEC-001 any depiction/voice/name of the Prophet; DEC-002 an uncovered Companion face, frontal close-up "
                       "or beard; DEC-003 explicit injury/gore), a historical anachronism a viewer would notice, broken anatomy or "
                       "identity that is obvious at normal viewing, or no candidate usable at all. Minor flaws (slight softness, "
                       "small grade drift, small camera-move deviation) are NOT real problems: pick the best and note them. "
                       "Write evidence to %s and write %s as JSON exactly: {\"choice\": \"<candidate file path as listed>\", "
                       "\"escalate\": false, \"reason\": \"why this one, >=20 chars\", \"candidates\": [{\"file\": ..., "
                       "\"notes\": ...}], \"evidence_files\": [\"reviews/evidence/...\"], \"obsolete_blockers\": [{\"id\": \"RV-...\", "
                       "\"why\": \"DEC-007: ...\"}]} (paths relative to projects/%s/). List in obsolete_blockers ONLY a BLOCKER "
                       "of the chosen candidate's earlier review that concerned faces of background/ensemble men now allowed by "
                       "DEC-007 (PRODUCER_DIRECTIVES.md); never a principal's face, the Prophet, injury or an anachronism. "
                       "Do not modify media. Do not run any review command.\n" % (p + x["evidence"], p + x["verdict"], pid))
    if k == "prompt_revise":
        return head + ("TASK (%s): the media item %s failed (%s). Revise the production prompt %s to fix the cause (change the "
                       "strategy: wording, framing, reference use, simplified action). Write a one-paragraph error analysis at "
                       "the end of the prompt file under '## Revision notes'.\n\nDETAILS:\n%s\n" % (
                           job["agent"], job["target"], job["extra"]["reason"], files, json.dumps(job.get("findings", []), indent=1)))
    if k == "connector":
        x = job["extra"]
        return head + ("TASK (%s): generate media for %s with the connected tool %s (capability %s, model %s), using the prompt "
                       "%s%s. The engine preflight passed (gates, holds, price %s, authorization %s). Save the result to %s "
                       "(download with curl if the tool returns a URL). Then record the REAL outcome with ONE standalone command (no cd, no &&, no pipes; quote values with spaces): `studio/bin/attempt-record "
                       "-p %s --shot %s --capability %s --phase %s --provider %s --model %s --outcome "
                       "SUCCESS|FAILED --output %s --cost <real cost> --authorization %s [--failure-category .. --analysis ..]`. "
                       "Never record SUCCESS without a real file.\n" % (
                           job["agent"], job["target"], ", ".join(x["tools"]), x["capability"], x["model"], p + x["prompt"],
                           (" with input " + p + x["input"]) if x.get("input") else "", x["price"], x["authorization"],
                           p + x["output"], pid, x["shot"], x["capability"], x["phase"], x["provider"], x["model"], x["output"],
                           x["authorization"])) + (("\nPROVIDER INSTRUCTIONS:\n%s\n" % x["instructions"]) if x.get("instructions") else "")
    if k == "diagnose":
        return head + ("TASK (executive-producer): review target %s failed %d independent review cycles. Read those cycles' "
                       "findings (reviews/verdicts, reviews/evidence) and diagnose the underlying cause. Write %s: the cause; "
                       "whether progress needs NEW SOURCE EVIDENCE, HUMAN JUDGEMENT, ADDITIONAL PERMISSIONS or an UNAVAILABLE "
                       "TOOL; and the exact question for the human (in Egyptian Arabic) plus the technical detail (English).\n"
                       % (job["target"], job["extra"]["failed"], p + job["extra"]["report"]))
    raise ValueError("unknown job kind %s" % k)


# ------------------------------------------------------------------ mock backend
class MockBackend:
    name = "mock"

    def __init__(self, plan=None):
        self.plan = plan or {}
        self.reviews_seen = {}

    def run(self, job, pdir, pid, crit_sets):
        time.sleep(float(self.plan.get("slow_s", 0)))
        k = job["kind"]
        if k == "intake":
            t = (pdir / "PROJECT.md").read_text(encoding="utf-8")
            t = t.replace("[[TBD]]", "MOCK value from brief")
            t += "\n## Brief (MOCK intake)\n%s\n" % job["extra"]["brief"]
            _write(pdir / "PROJECT.md", t)
        elif k == "create":
            for f in job["files"]:
                if f == "SHOT_LIST.csv":
                    self._shot_list(pdir, job)
                else:
                    _write(pdir / f, "# MOCK %s\nSynthetic test deliverable created by %s for stage %s. MOCK - not real content.\n"
                           % (f, job["agent"], job["stage"]))
        elif k == "revise":
            for f in job["files"]:
                path = pdir / f
                old = path.read_text(encoding="utf-8") if path.exists() else ""
                ids = ", ".join(x.get("id", "?") for x in job.get("findings", []))
                if f.endswith(".csv"):
                    rows = old.rstrip("\n").split("\n")
                    rows[-1] = rows[-1].replace("MOCK shot", "MOCK shot (rev)", 1)
                    _write(path, "\n".join(rows) + "\n")
                else:
                    _write(path, old + "\n## MOCK revision for %s addressing %s\n" % (job.get("cycle_id"), ids))
        elif k == "report":
            _write(pdir / job["extra"]["report"], "# MOCK report by %s\nAbout: %s\nInspection: synthetic test media only.\n"
                   % (job["agent"], ", ".join(job["extra"].get("about", []))))
        elif k == "review":
            self._review(job, pdir, crit_sets)
        elif k == "prompt_revise":
            for f in job["files"]:
                path = pdir / f
                _write(path, path.read_text(encoding="utf-8") + "\n## Revision notes (MOCK)\nStrategy change after %s.\n"
                       % job["extra"]["reason"])
        elif k == "diagnose":
            _write(pdir / job["extra"]["report"], "# MOCK diagnosis for %s\nCause: synthetic failure plan. Needs: HUMAN JUDGEMENT.\n"
                   % job["target"])
        else:
            return {"status": "FAILED", "cost_usd": 0.0, "summary": "mock backend cannot run %s jobs" % k}
        return {"status": "OK", "cost_usd": 0.0, "summary": "MOCK %s done" % k}

    def _shot_list(self, pdir, job):
        cols = ("shot_id,scene_id,seq,description,shot_type,camera_angle,camera_movement,lens_mm,duration_s,characters,"
                "environment_id,props,time_of_day,weather,dialogue_ref,sfx_notes,music_cue,transition_in,transition_out,"
                "image_prompt,video_prompt,ref_assets,status,retries,notes").split(",")
        n = int(self.plan.get("shots", 4))
        secs = float(self.plan.get("shot_seconds", 2))
        hold_tag = (job.get("extra", {}).get("holds") or [{}])[0].get("tag")
        lines = [",".join(cols)]
        for i in range(1, n + 1):
            sid = "SH-001-%02d" % i
            notes = "%s explicit injury (MOCK)" % hold_tag if hold_tag and i == int(self.plan.get("hold_shot", 0)) else ""
            row = {"shot_id": sid, "scene_id": "SC-001", "seq": str(i), "description": "MOCK shot %d" % i,
                   "shot_type": "wide" if i % 2 else "closeup", "duration_s": str(secs), "characters": "CH-001",
                   "environment_id": "ENV-001", "dialogue_ref": "SC-001-D%d" % i if i % 2 == 0 else "",
                   "image_prompt": "prompts/images/%s.md" % sid, "video_prompt": "prompts/video/%s.md" % sid,
                   "status": "PLANNED", "retries": "0", "notes": notes}
            lines.append(",".join(row.get(c, "") for c in cols))
            _write(pdir / ("prompts/images/%s.md" % sid), "MOCK keyframe prompt for %s\n" % sid)
            _write(pdir / ("prompts/video/%s.md" % sid), "MOCK motion prompt for %s\n" % sid)
            if i % 2 == 0:
                _write(pdir / ("prompts/audio/%s.md" % sid), "MOCK line of English dialogue for %s\n" % sid)
        _write(pdir / "prompts/audio/MUSIC_music.md", "MOCK score prompt\n")
        _write(pdir / "prompts/audio/AMBIENCE_sfx.md", "MOCK ambience prompt\n")
        _write(pdir / "SHOT_LIST.csv", "\n".join(lines) + "\n")

    def _review(self, job, pdir, crit_sets):
        target = job["target"]
        seen = self.reviews_seen.get(target, 0)
        self.reviews_seen[target] = seen + 1
        fail = target in self.plan.get("always_fail", []) or seen < int(self.plan.get("fail_reviews", {}).get(target, 0))
        ev = job["extra"]["evidence"]
        _write(pdir / ev, "# MOCK evidence for %s (%s)\nSynthetic review of synthetic content. Inspection files: %s\n"
               % (job["cycle_id"], target, ", ".join(job["extra"].get("inspections", [])) or "n/a"))
        efiles = [ev] + [x for x in job["extra"].get("inspections", []) if (pdir / x).exists()]
        crits, findings = {}, []
        for i, c in enumerate(crit_sets[job["set"]]):
            if fail and i == 0:
                crits[c["id"]] = {"result": "FAIL", "evidence": "MOCK planned failure for repair-loop testing (%s)" % target,
                                  "evidence_files": efiles}
                findings.append({"id": "RV-M%02d" % (seen + 1), "severity": "MAJOR", "where": target,
                                 "issue": "MOCK planned defect", "required_fix": "MOCK fix"})
            else:
                crits[c["id"]] = {"result": "PASS", "evidence": "MOCK pass for %s on %s" % (c["id"], target),
                                  "evidence_files": efiles}
        _write(pdir / job["extra"]["verdict"], json.dumps({"criteria": crits, "summary": "MOCK review of %s" % target,
                                                          "findings": findings}, indent=1))


# ------------------------------------------------------------------ real backend
def find_claude():
    env = os.environ.get("STUDIO_CLAUDE_BIN")
    if env and os.access(env, os.X_OK):
        return env
    w = shutil.which("claude")
    if w:
        return w
    cands = []
    for pat in ("~/.antigravity-ide/extensions/anthropic.claude-code-*/resources/native-binary/claude",
                "~/.vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude",
                "~/.cursor/extensions/anthropic.claude-code-*/resources/native-binary/claude",
                "~/.claude/local/claude"):
        cands += glob.glob(os.path.expanduser(pat))

    def ver(p):
        m = re.search(r"claude-code-(\d+)\.(\d+)\.(\d+)", p)
        return tuple(int(x) for x in m.groups()) if m else (0,)
    for c in sorted(cands, key=ver, reverse=True):
        if os.access(c, os.X_OK):
            try:
                if subprocess.run([c, "--version"], capture_output=True, timeout=30).returncode == 0:
                    return c
            except (OSError, subprocess.SubprocessError):
                continue
    return None


def agent_tools(agent):
    p = STUDIO_ROOT / ".claude" / "agents" / ("%s.md" % agent)
    if not p.exists():
        raise ValueError("unknown agent %s" % agent)
    m = re.search(r"^tools:\s*(.+)$", p.read_text(encoding="utf-8"), re.M)
    return [t.strip() for t in m.group(1).split(",")] if m else []


TRANSIENT_RE = re.compile(r"(?i)(rate.?limit|overloaded|529|503|ECONNRESET|ETIMEDOUT|network|temporarily|hit your .*limit)")


class ClaudeBackend:
    name = "claude"

    def __init__(self, bin_path=None, per_job_cap_usd=15, timeout_s=5400, max_turns=200):
        self.bin = bin_path or find_claude()
        self.cap = per_job_cap_usd
        self.timeout = timeout_s
        self.max_turns = max_turns

    def available(self):
        return bool(self.bin)

    def command(self, job, pid, prompt):
        tools = [t for t in agent_tools(job["agent"]) if t not in ("Bash", "Agent", "SendMessage")]
        allowed = tools + ["Bash(python3 studio/studio.py %s:*)" % c for c in SAFE_BASH]
        allowed += job.get("extra", {}).get("allow_tools", [])
        disallowed = ["Bash(python3 studio/studio.py %s:*)" % c for c in DENY_ENGINE
                      if not any(a.startswith("Bash(python3 studio/studio.py %s" % c) for a in allowed)]
        cmd = [self.bin, "-p", prompt, "--agent", job["agent"], "--permission-mode", "acceptEdits",
               "--output-format", "json", "--max-turns", str(self.max_turns)]
        # mechanical jobs (drive a connected tool and record the attempt) do not need the strongest model;
        # judgement jobs (reviews, picks, writing) keep the agent's own model
        fast = os.environ.get("STUDIO_FAST_MODEL", "sonnet")
        if fast and job.get("kind") in ("connector",):
            cmd += ["--model", fast]
        if self.cap:
            cmd += ["--max-budget-usd", str(self.cap)]
        mcfg = job.get("extra", {}).get("mcp_config")
        if mcfg:
            cmd += ["--mcp-config", str(STUDIO_ROOT / mcfg)]
            if job.get("extra", {}).get("mcp_strict"):
                cmd += ["--strict-mcp-config"]
        for d in job.get("extra", {}).get("add_dirs") or []:
            cmd += ["--add-dir", os.path.expanduser(d)]
        cmd += ["--allowedTools"] + allowed + ["--disallowedTools"] + disallowed
        return cmd

    def run(self, job, pdir, pid, crit_sets, job_dir=None):
        if not self.bin:
            return {"status": "FAILED", "cost_usd": 0.0, "summary": "NO_CLAUDE_CLI: Claude Code CLI not found "
                    "(set STUDIO_CLAUDE_BIN)"}
        prompt = brief(job, pid)
        cmd = self.command(job, pid, prompt)
        if job_dir:
            _write(Path(job_dir) / "brief.md", prompt)
            _write(Path(job_dir) / "command.json", json.dumps(cmd[:2] + ["<brief>"] + cmd[3:], indent=1))
        try:
            # connectors (claude.ai MCP, local MCP) must be connected before the first turn of a headless job
            env = dict(os.environ, MCP_CONNECTION_NONBLOCKING="false", MCP_CONNECTOR_PREWAIT_MS="30000")
            r = subprocess.run(cmd, cwd=str(STUDIO_ROOT), capture_output=True, text=True, timeout=self.timeout,
                               stdin=subprocess.DEVNULL, env=env)
        except subprocess.TimeoutExpired:
            return {"status": "FAILED", "cost_usd": 0.0, "summary": "TIMEOUT after %ss" % self.timeout}
        if job_dir:
            _write(Path(job_dir) / "result.json", r.stdout or "")
            _write(Path(job_dir) / "stderr.txt", r.stderr or "")
        data = {}
        try:
            data = json.loads((r.stdout or "").strip().splitlines()[-1])
        except (ValueError, IndexError):
            pass
        cost = float(data.get("total_cost_usd") or 0)
        text = (data.get("result") or "") + " " + (r.stderr or "")
        if r.returncode != 0 or data.get("is_error"):
            if TRANSIENT_RE.search(text):
                return {"status": "TRANSIENT", "cost_usd": cost, "summary": text.strip()[:300], "retry_after_s": 1800}
            return {"status": "FAILED", "cost_usd": cost, "summary": ("%s: %s" % (data.get("subtype"), text.strip()))[:400]}
        return {"status": "OK", "cost_usd": cost, "summary": (data.get("result") or "")[:600],
                "session_id": data.get("session_id")}

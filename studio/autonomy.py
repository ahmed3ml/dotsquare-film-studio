"""Autonomy controls for studio.py: the AUTO_APPROVAL policy, external (human) requirements, content holds and the
delivery spec. These are ENGINE-ENFORCED: the orchestrator (or anyone) can only obtain a policy approval when every
condition below holds, and mandatory human/external approvals can never be granted by policy.

Policy file: projects/<ID>/approval_policy.json (copied from standards/approval_policy.json; agents cannot edit it -
the PreToolUse hook blocks it; humans change it with `policy ...` commands, which are permission-prompted).
"""
import copy
import json
import os
from pathlib import Path

C = None  # core module, set by register()
X = None  # ext module

POLICY_FILE = "approval_policy.json"
POLICY_APPROVER = "POLICY:AUTO_APPROVAL"
# Never policy-approvable, whatever a policy file says: GATE-07 approves the production budget and execution plan
# (spending scope). Final external publication/distribution is never performed by the engine at all.
HARD_HUMAN_GATES = {"GATE-07"}


def default_policy():
    return json.loads((C.STUDIO_ROOT / "standards" / "approval_policy.json").read_text(encoding="utf-8"))


def policy_path(pdir):
    return Path(pdir) / POLICY_FILE


def load_policy(pdir):
    p = policy_path(pdir)
    if not p.exists():
        pol = default_policy()
        pol["_source"] = "standards default (no project policy file)"
        pol["auto_approval"]["enabled"] = False  # no project policy => no policy approvals
        return pol
    return json.loads(p.read_text(encoding="utf-8"))


def save_policy(pdir, pol):
    pol.pop("_source", None)
    tmp = policy_path(pdir).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(pol, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, policy_path(pdir))


def policy_sha(pdir):
    p = policy_path(pdir)
    return C.sha256_file(p)[:16] if p.exists() else "none"


def install_policy(pdir, overrides=None):
    pol = default_policy()
    for k, v in (overrides or {}).items():
        pol[k] = v
    save_policy(pdir, pol)
    return pol


def gate_mode(pol, gid):
    if gid in HARD_HUMAN_GATES or gid in pol.get("always_human_gates", []) or gid in pol.get("client_approval_gates", []):
        return "human"
    return pol.get("gate_modes", {}).get(gid, "human")


def external_pending(pol, gid):
    return [r for r in pol.get("external_requirements", []) if r.get("gate") == gid
            and r.get("status") not in ("SATISFIED", "WAIVED")]


def scope_problem(pdir, pol):
    lock = pol.get("scope_lock") or {}
    f = Path(pdir) / lock.get("file", "PROJECT.md")
    if not lock.get("sha256"):
        return "SCOPE_NOT_ACCEPTED: no accepted project scope (`policy accept-scope --by <human>`)"
    if not f.exists() or C.sha256_file(f) != lock["sha256"]:
        return "SCOPE_CHANGED: %s changed since the scope was accepted by %s; a human must re-accept it" % (
            lock.get("file", "PROJECT.md"), lock.get("by"))
    return None


def approval_problems(pdir, st, gid, by_policy):
    """Reasons `approve` must refuse. Applies to human approvals too (external requirements)."""
    pol = load_policy(pdir)
    problems = []
    for r in external_pending(pol, gid):
        problems.append("EXTERNAL_REQUIRED: %s (%s) is %s; record it with `external record --req %s --by <human> "
                        "--evidence <file>` before %s can be approved" % (r["id"], r.get("description", ""),
                                                                           r.get("status"), r["id"], gid))
    if not by_policy:
        return problems
    if not pol.get("auto_approval", {}).get("enabled"):
        problems.append("POLICY_DISABLED: auto-approval is not enabled for this project (%s)" % pol.get("_source", POLICY_FILE))
    if gate_mode(pol, gid) != "policy":
        why = "hard human gate (budget/spending)" if gid in HARD_HUMAN_GATES else (
            "client approval recorded for this project" if gid in pol.get("client_approval_gates", []) else "policy mode is human")
        problems.append("HUMAN_REQUIRED: %s needs a human approval (%s)" % (gid, why))
    for cset in X.criteria()["gate_reviews"].get(gid, []):
        cyc = X.latest_cycle(st, "gate:" + gid, cset)
        if not cyc or cyc["status"] not in ("ACCEPTED", "ACCEPTED_BY_HUMAN", "ACCEPTED_BY_POLICY"):
            problems.append("REVIEW_NOT_ACCEPTED: %s" % cset)
            continue
        if not X.files_unchanged(pdir, cyc["files"]):
            problems.append("REVIEW_STALE: %s" % cyc["id"])
        if any(f.get("severity") == "BLOCKER" for f in cyc.get("findings", [])):
            problems.append("BLOCKING_FINDINGS: %s has BLOCKER findings" % cyc["id"])
    sp = scope_problem(pdir, pol)
    if sp:
        problems.append(sp)
    b = st["budget"]
    _, act = C.cost_totals(pdir)
    if b["approved_total"] is not None and act > b["approved_total"] + 1e-9:
        problems.append("OVER_BUDGET: actual %.2f exceeds approved %.2f" % (act, b["approved_total"]))
    if b["approved_total"] is None and act > 0:
        problems.append("SPEND_WITHOUT_BUDGET: actual spend %.2f without an approved budget" % act)
    if st.get("blockers"):
        problems.append("OPEN_BLOCKERS: %d project blockers" % len(st["blockers"]))
    return problems


# ------------------------------------------------------------------ content holds (e.g. CA-001)
def active_holds(pdir):
    return [h for h in load_policy(pdir).get("content_holds", []) if h.get("status") != "RELEASED"]


def shot_holds(pdir, shot_id):
    """Holds that apply to a shot: a hold tag (e.g. [CA-001]) in the shot's description or notes."""
    path = Path(pdir) / "SHOT_LIST.csv"
    if not path.exists():
        return []
    row = next((r for r in C.read_csv(path) if r["shot_id"] == shot_id), None)
    if not row:
        return []
    text = " ".join([row.get("description", ""), row.get("notes", "")])
    return [h for h in active_holds(pdir) if h.get("tag") and h["tag"] in text]


def hold_check(pdir, shot_id):
    hs = shot_holds(pdir, shot_id)
    if hs:
        raise C.StudioError("HOLD_ACTIVE: %s is tagged %s (%s) - generation is not allowed until a human records "
                            "the release (`hold release --id %s --by <client> --evidence <file>`)" % (
                                shot_id, hs[0]["tag"], hs[0].get("description", ""), hs[0]["id"]))


# ------------------------------------------------------------------ commands
def _need_human(args):
    if not args.by or not args.by.strip() or args.by.strip().upper().startswith("POLICY"):
        raise C.StudioError("--by <human name> is required (this is a human decision)")


def _evidence(pdir, rel):
    if not rel or not (Path(pdir) / rel).exists():
        raise C.StudioError("--evidence <file inside the project> is required and must exist (documented evidence)")
    return {"file": rel, "sha256": C.sha256_file(Path(pdir) / rel)}


def cmd_policy(args):
    pdir = C.project_dir(args)
    st = C.load(pdir)
    pol = load_policy(pdir)
    if args.action == "show":
        print("policy file: %s (sha %s)" % (POLICY_FILE if policy_path(pdir).exists() else pol.get("_source"), policy_sha(pdir)))
        print("auto_approval enabled: %s" % pol["auto_approval"]["enabled"])
        for gid in C.GATE_IDS:
            print("  %s  %-6s %s" % (gid, gate_mode(pol, gid),
                                    "; ".join("needs %s (%s)" % (r["id"], r.get("status")) for r in
                                              pol.get("external_requirements", []) if r.get("gate") == gid)))
        for h in pol.get("content_holds", []):
            print("  hold %s tag=%s status=%s" % (h["id"], h.get("tag"), h.get("status")))
        print("scope: %s" % (scope_problem(pdir, pol) or "accepted by %s" % pol["scope_lock"].get("by")))
        rl = pol.get("review_loop", {})
        print("best-of-N pick: %s (max media attempts %s, by %s)" % (rl.get("pick_best", False), rl.get("max_media_attempts", "-"),
                                                                    rl.get("pick_best_by")))
        print("unattended paid generation: %s (authorization %s)" % (
            pol["spending"].get("unattended_paid_generation"), pol["spending"].get("authorization")))
        return 0
    if args.action == "check":
        gid = (args.gate or "").upper()
        C.gate_def(gid)
        probs = approval_problems(pdir, st, gid, by_policy=True)
        print("%s policy-approvable: %s" % (gid, "YES" if not probs else "NO"))
        for p in probs:
            print("  - " + p)
        return 0 if not probs else 1
    _need_human(args)
    if not policy_path(pdir).exists():
        pol = default_policy()
    if args.action == "set":
        gid = (args.gate or "").upper()
        C.gate_def(gid)
        if args.mode not in ("policy", "human"):
            raise C.StudioError("--mode policy|human")
        if args.mode == "policy" and gid in HARD_HUMAN_GATES:
            raise C.StudioError("%s is a hard human gate and cannot be policy-approved" % gid)
        if args.mode == "policy" and gid in pol.get("client_approval_gates", []):
            pol["client_approval_gates"].remove(gid)
        pol.setdefault("gate_modes", {})[gid] = args.mode
    elif args.action in ("enable", "disable"):
        pol["auto_approval"]["enabled"] = args.action == "enable"
    elif args.action == "accept-scope":
        f = Path(pdir) / pol["scope_lock"].get("file", "PROJECT.md")
        pol["scope_lock"].update(sha256=C.sha256_file(f), by=args.by, at=C.now())
    elif args.action == "paid-generation":
        if args.enable and not args.authorization:
            raise C.StudioError("--authorization AU-xxxx (an existing human cost authorization) is required")
        if args.enable and not any(a["id"] == args.authorization for a in st["authorizations"]):
            raise C.StudioError("unknown authorization %s" % args.authorization)
        pol["spending"].update(unattended_paid_generation=bool(args.enable),
                               authorization=args.authorization if args.enable else None)
    elif args.action == "pick-best":
        rl = pol.setdefault("review_loop", {})
        rl["pick_best"] = bool(args.enable)
        rl["max_media_attempts"] = int(args.amount or 2)
        rl["pick_best_by"] = args.by if args.enable else None
        rl["final_attempt"] = bool(args.enable)
    elif args.action == "llm-budget":
        if args.amount is None or args.amount < 0:
            raise C.StudioError("--amount USD (>= 0; 0 removes the cap) is required")
        pol.setdefault("spending", {})["llm_budget_usd"] = args.amount or None
    save_policy(pdir, pol)
    C.log_gate(pdir, "POLICY", "POLICY_%s %s" % (args.action.upper(), args.gate or args.authorization or ""), args.by,
               args.note or "", version=policy_sha(pdir))
    print("policy %s saved (sha %s)" % (args.action, policy_sha(pdir)))
    return 0


def cmd_external(args):
    pdir = C.project_dir(args)
    pol = load_policy(pdir)
    if args.action == "list":
        for r in pol.get("external_requirements", []):
            print("%-20s gate=%s status=%s %s" % (r["id"], r.get("gate"), r.get("status"), r.get("description", "")))
        return 0
    _need_human(args)
    r = next((x for x in pol.get("external_requirements", []) if x["id"] == args.req), None)
    if not r:
        raise C.StudioError("unknown external requirement %s" % args.req)
    if args.action == "waive":
        # A human waiver is NOT the external review: the task stays open and the waiver is logged as such.
        if not args.note:
            raise C.StudioError("--note (reason and scope of the waiver) is required")
        r.update(status="WAIVED", waived_by=args.by, at=C.now(), note=args.note)
        save_policy(pdir, pol)
        C.log_gate(pdir, r.get("gate", "EXTERNAL"), "EXTERNAL_REQUIREMENT_WAIVED %s" % r["id"], args.by,
                   args.note + " (review NOT performed)")
        print("%s WAIVED by %s (the review itself has NOT happened; task left open)" % (r["id"], args.by))
        return 0
    ev = _evidence(pdir, args.evidence)
    r.update(status="SATISFIED", recorded_by=args.by, at=C.now(), evidence=ev, note=args.note or "")
    save_policy(pdir, pol)
    C.log_gate(pdir, r.get("gate", "EXTERNAL"), "EXTERNAL_REQUIREMENT_SATISFIED %s" % r["id"], args.by,
               (args.note or "") + " evidence=%s sha=%s" % (ev["file"], ev["sha256"][:16]))
    if r.get("task"):
        st = C.load(pdir)
        t = next((x for x in st["tasks"] if x["id"] == r["task"]), None)
        if t:
            t["status"] = "DONE"
            C.save(pdir, st)
    print("%s SATISFIED (recorded by %s)" % (r["id"], args.by))
    return 0


def cmd_hold(args):
    pdir = C.project_dir(args)
    pol = load_policy(pdir)
    if args.action == "list":
        for h in pol.get("content_holds", []):
            print("%-8s tag=%s status=%s %s" % (h["id"], h.get("tag"), h.get("status"), h.get("description", "")))
        return 0
    _need_human(args)
    h = next((x for x in pol.get("content_holds", []) if x["id"] == args.id), None)
    if not h:
        raise C.StudioError("unknown hold %s" % args.id)
    ev = _evidence(pdir, args.evidence)
    h.update(status="RELEASED", released_by=args.by, at=C.now(), evidence=ev, note=args.note or "")
    save_policy(pdir, pol)
    C.log_gate(pdir, "HOLD", "HOLD_RELEASED %s" % h["id"], args.by,
               (args.note or "") + " evidence=%s sha=%s" % (ev["file"], ev["sha256"][:16]))
    if h.get("task"):
        st = C.load(pdir)
        t = next((x for x in st["tasks"] if x["id"] == h["task"]), None)
        if t:
            t["status"] = "DONE"
            C.save(pdir, st)
    print("%s RELEASED by %s" % (h["id"], args.by))
    return 0


def cmd_spec(args):
    pdir = C.project_dir(args)
    st = C.load(pdir)
    if args.action == "show":
        print(json.dumps(st.get("delivery_spec"), indent=2))
        return 0
    _need_human(args)
    spec = {}
    for k in ("width", "height", "duration_s"):
        v = getattr(args, k)
        if v is not None:
            spec[k] = int(v) if k != "duration_s" else float(v)
    if args.fps is not None:
        spec["fps"] = float(args.fps)
    for k in ("video_codec", "audio_codec"):
        if getattr(args, k):
            spec[k] = getattr(args, k)
    if args.tolerance is not None:
        spec["duration_tolerance_pct"] = float(args.tolerance)
    if not spec:
        raise C.StudioError("give at least one of --width --height --fps --duration-s --video-codec --audio-codec")
    st["delivery_spec"] = spec
    C.save(pdir, st)
    C.log_gate(pdir, "SPEC", "DELIVERY_SPEC_SET %s" % json.dumps(spec), args.by, "")
    print("delivery_spec set: %s" % json.dumps(spec))
    return 0


def validate_policy(pdir, st):
    errors, warns = [], []
    p = policy_path(pdir)
    if not p.exists():
        return errors, warns
    try:
        pol = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        return ["POLICY_INVALID_JSON: %s" % e], warns
    for gid, mode in pol.get("gate_modes", {}).items():
        if gid not in C.GATE_IDS or mode not in ("policy", "human"):
            errors.append("POLICY_BAD_GATE_MODE: %s=%s" % (gid, mode))
        if gid in HARD_HUMAN_GATES and mode == "policy":
            errors.append("POLICY_HARD_GATE: %s can never be policy-approved" % gid)
    for gid, g in st["gates"].items():
        if g["state"] == "APPROVED" and g.get("approved_by") == POLICY_APPROVER and gate_mode(pol, gid) != "policy":
            warns.append("POLICY_APPROVED_BUT_NOW_HUMAN: %s was policy-approved; policy has since changed" % gid)
    return errors, warns


def register(core, ext_mod, add):
    global C, X
    C, X = core, ext_mod
    core.VALIDATE_CHECKS.append(validate_policy)
    sp = add("policy", cmd_policy)
    sp.add_argument("action", choices=["show", "check", "set", "enable", "disable", "accept-scope", "paid-generation",
                                       "llm-budget", "pick-best"])
    sp.add_argument("--amount", type=float, help="llm-budget: total USD cap for agent compute (0 = no cap)")
    sp.add_argument("--gate")
    sp.add_argument("--mode")
    sp.add_argument("--by")
    sp.add_argument("--note")
    sp.add_argument("--authorization")
    sp.add_argument("--enable", action="store_true")
    sp = add("external", cmd_external)
    sp.add_argument("action", choices=["list", "record", "waive"])
    sp.add_argument("--req")
    sp.add_argument("--by")
    sp.add_argument("--evidence")
    sp.add_argument("--note")
    sp = add("hold", cmd_hold)
    sp.add_argument("action", choices=["list", "release"])
    sp.add_argument("--id")
    sp.add_argument("--by")
    sp.add_argument("--evidence")
    sp.add_argument("--note")
    sp = add("spec", cmd_spec)
    sp.add_argument("action", choices=["show", "set"])
    sp.add_argument("--width", type=int)
    sp.add_argument("--height", type=int)
    sp.add_argument("--fps", type=float)
    sp.add_argument("--duration-s", dest="duration_s", type=float)
    sp.add_argument("--video-codec", dest="video_codec")
    sp.add_argument("--audio-codec", dest="audio_codec")
    sp.add_argument("--tolerance", type=float)
    sp.add_argument("--by")

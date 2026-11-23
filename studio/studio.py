#!/usr/bin/env python3
"""AI Film Studio workflow engine (stdlib only, Python 3.9+).

The only sanctioned way to change gate/stage/task state. Enforces: sequential gates, human-only
approvals (by convention + permission prompt), file locking by sha256, no placeholder submissions,
append-only logs, cost authorization and no credential leakage. It never calls any paid service.
"""
import argparse
import csv
import datetime
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ext  # noqa: E402
import autonomy  # noqa: E402

HERE = Path(__file__).resolve().parent
STUDIO_ROOT = HERE.parent
TEMPLATES = STUDIO_ROOT / "templates"
ADAPTERS_JSON = STUDIO_ROOT / "adapters" / "adapters.json"
PLACEHOLDER = "[[TBD]]"
SCHEMA_VERSION = "1.1"
SUBMIT_CHECKS = []    # fn(pdir, st, gate) -> [problems]; registered by ext
VALIDATE_CHECKS = []  # fn(pdir, st) -> (errors, warns)

# ---------------------------------------------------------------- definitions
GATES = [
    ("GATE-01", "Historical research and story concept", "البحث التاريخي وفكرة القصة", ["HISTORICAL_BIBLE.md"]),
    ("GATE-02", "Story treatment", "المعالجة القصصية", ["STORY_TREATMENT.md"]),
    ("GATE-03", "Final screenplay", "السيناريو النهائي", ["SCREENPLAY.md", "reviews/SCRIPT_REVIEW.md"]),
    ("GATE-04", "Character designs and visual bible", "تصميم الشخصيات والدليل البصري",
     ["CHARACTER_BIBLE.md", "ENVIRONMENT_BIBLE.md", "VISUAL_STYLE_GUIDE.md"]),
    ("GATE-05", "Storyboard and shot list", "اللوحة القصصية وقائمة اللقطات", ["SHOT_LIST.csv"]),
    ("GATE-06", "Initial proof-of-concept video", "فيديو إثبات المفهوم",
     ["media/proof_of_concept/*.mp4", "media/proof_of_concept/PROOF_REPORT.md"]),
    ("GATE-07", "Full-production budget and execution plan", "الميزانية وخطة التنفيذ الكاملة",
     ["PRODUCTION_PLAN.md", "COST_TRACKER.csv"]),
    ("GATE-08", "Rough cut", "النسخة المبدئية (Rough Cut)", ["media/rough_cut/*.mp4", "reviews/QC_ROUGH_CUT.md"]),
    ("GATE-09", "Final film delivery", "التسليم النهائي للفيلم",
     ["media/final/*.mp4", "DELIVERY_CHECKLIST.md", "reviews/QC_FINAL.md"]),
]
GATE_IDS = [g[0] for g in GATES]

# name, requires_gate, requires_stages, produces_gate, owner agent, skills
STAGES = [
    ("research", None, [], "GATE-01", "historical-researcher", "historical-research"),
    ("story", "GATE-01", ["research"], "GATE-02", "story-developer", "story-development"),
    ("screenplay", "GATE-02", ["story"], None, "screenwriter", "screenplay-writing"),
    ("script_review", "GATE-02", ["screenplay"], "GATE-03", "script-reviewer", "script-review"),
    ("directing", "GATE-03", [], None, "film-director", "film-directing"),
    ("visual_development", "GATE-03", [], "GATE-04", "visual-development-supervisor",
     "character-design, production-design"),
    ("storyboard", "GATE-04", ["directing"], "GATE-05", "shot-planner", "storyboard-planning"),
    ("proof_of_concept", "GATE-05", [], "GATE-06", "video-producer",
     "ai-image-production, ai-video-production"),
    ("production_planning", "GATE-06", [], "GATE-07", "executive-producer", "film-project-manager"),
    ("image_production", "GATE-07", [], None, "image-producer", "ai-image-production"),
    ("video_production", "GATE-07", [], None, "video-producer", "ai-video-production"),
    ("sound", "GATE-07", [], None, "sound-producer", "sound-direction"),
    ("editing", "GATE-07", ["video_production", "sound"], "GATE-08", "film-editor", "film-editing"),
    ("quality_control", "GATE-08", ["editing"], None, "film-quality-reviewer", "film-quality-control"),
    ("delivery", "GATE-08", ["quality_control"], "GATE-09", "executive-producer", "film-project-manager"),
]
STAGE_NAMES = [s[0] for s in STAGES]
STAGE_BY = {s[0]: s for s in STAGES}

STAGE_STATES = ["NOT_STARTED", "IN_PROGRESS", "DONE", "BLOCKED", "BLOCKED_EXTERNAL_TOOL"]
TASK_STATES = ["TODO", "IN_PROGRESS", "DONE", "BLOCKED"]
SHOT_STATES = ["PLANNED", "PROMPT_READY", "IMAGE_DONE", "VIDEO_DONE", "IN_REVIEW", "APPROVED", "RETRY",
               "FAILED", "BLOCKED_EXTERNAL_TOOL"]

ROOT_FILES = ["PROJECT.md", "PROJECT_STATUS.json", "HISTORICAL_BIBLE.md", "STORY_TREATMENT.md", "SCREENPLAY.md",
              "CHARACTER_BIBLE.md", "ENVIRONMENT_BIBLE.md", "VISUAL_STYLE_GUIDE.md", "SHOT_LIST.csv",
              "PRODUCTION_LOG.csv", "APPROVAL_LOG.md", "CHANGE_LOG.md", "COST_TRACKER.csv",
              "DELIVERY_CHECKLIST.md"]
EXTRA_FILES = ["GENERATION_LOG.csv", "PRODUCTION_PLAN.md", "reviews/SCRIPT_REVIEW.md", "reviews/QC_ROUGH_CUT.md", "reviews/QC_FINAL.md"]
TEMPLATE_MAP = {"reviews/SCRIPT_REVIEW.md": "SCRIPT_REVIEW.md", "reviews/QC_ROUGH_CUT.md": "QC_REPORT.md",
                "reviews/QC_FINAL.md": "QC_REPORT.md"}
CSV_HEADERS = {
    "SHOT_LIST.csv": "shot_id,scene_id,seq,description,shot_type,camera_angle,camera_movement,lens_mm,duration_s,"
                     "characters,environment_id,props,time_of_day,weather,dialogue_ref,sfx_notes,music_cue,"
                     "transition_in,transition_out,image_prompt,video_prompt,ref_assets,status,retries,notes",
    "PRODUCTION_LOG.csv": "log_id,timestamp,kind,task_id,stage,owner,action,status,tool,cost_usd,details",
    "GENERATION_LOG.csv": "attempt_id,timestamp,shot_id,capability,phase,provider,model,kind,outcome,failure_category,error_analysis,strategy_change,cost_usd,authorization,output_path,output_sha256,synthetic,notes",
    "COST_TRACKER.csv": "entry_id,date,kind,stage,item,units,unit_cost,amount,currency,authorized_by,notes",
}
SHOT_MUTABLE_COLS = {"status", "retries", "ref_assets", "notes"}
FILE_LOCK_MODE = {"COST_TRACKER.csv": "prefix", "SHOT_LIST.csv": "csv-planning"}
ID_RE = {"project": r"^[A-Z0-9]+(-[A-Z0-9]+)*$", "scene": r"^SC-\d{3}[A-Z]?$",
         "shot": r"^SH-\d{3}[A-Z]?-\d{2}$", "task": r"^TK-\d{4}$"}
SECRET_RES = [re.compile(r"sk-[A-Za-z0-9_\-]{20,}"), re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"]?[A-Za-z0-9_\-\.]{16,}"),
              re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.]{20,}"), re.compile(r"AKIA[0-9A-Z]{16}")]

AR = {
    "NOT_STARTED": "لم تبدأ", "PREPARING": "قيد التحضير", "PENDING_APPROVAL": "بانتظار موافقتك",
    "APPROVED": "معتمدة", "REVISION_REQUESTED": "مطلوب تعديل", "REJECTED": "مرفوضة", "STALE": "تحتاج مراجعة (تغيّر ما قبلها)",
    "IN_PROGRESS": "قيد التنفيذ", "DONE": "مكتملة", "BLOCKED": "متوقفة",
    "BLOCKED_EXTERNAL_TOOL": "متوقفة: أداة خارجية غير متصلة",
}


class StudioError(Exception):
    pass


# ---------------------------------------------------------------- helpers
def now():
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def today():
    return datetime.datetime.utcnow().date().isoformat()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def projects_dir(args=None):
    p = (getattr(args, "projects_dir", None) if args else None) or os.environ.get("STUDIO_PROJECTS_DIR")
    return Path(p) if p else STUDIO_ROOT / "projects"


def project_dir(args):
    base = projects_dir(args)
    pid = getattr(args, "project", None)
    if not pid:
        found = sorted(d.name for d in base.iterdir() if (d / "PROJECT_STATUS.json").exists()) if base.exists() else []
        if len(found) == 1:
            pid = found[0]
        else:
            raise StudioError("Specify --project/-p (available: %s)" % (", ".join(found) or "none"))
    d = base / pid
    if not (d / "PROJECT_STATUS.json").exists():
        raise StudioError("Project not found: %s" % pid)
    return d


def load(pdir):
    with open(pdir / "PROJECT_STATUS.json", encoding="utf-8") as f:
        return json.load(f)


def save(pdir, st):
    st["updated_at"] = now()
    tmp = pdir / "PROJECT_STATUS.json.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, pdir / "PROJECT_STATUS.json")


def append_text(path, text):
    with open(path, "a", encoding="utf-8") as f:
        f.write(text)


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def append_csv(path, header, row):
    cols = header.split(",")
    with open(path, "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=cols).writerow({c: row.get(c, "") for c in cols})


def next_id(prefix, existing, width):
    nums = [int(x[len(prefix) + 1:]) for x in existing if x.startswith(prefix + "-") and x[len(prefix) + 1:].isdigit()]
    return "%s-%0*d" % (prefix, width, (max(nums) + 1) if nums else 1)


def gate_def(gid):
    gid = gid.upper()
    if gid not in GATE_IDS:
        raise StudioError("Unknown gate %s (use GATE-01..GATE-09)" % gid)
    return next(g for g in GATES if g[0] == gid)


def has_ffprobe():
    return shutil.which("ffprobe") is not None


def probe(path):
    if not has_ffprobe():
        raise StudioError("ffprobe not available: media NOT_VERIFIED")
    r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise StudioError("ffprobe failed on %s: %s" % (path, r.stderr.strip()[:200]))
    data = json.loads(r.stdout)
    out = {"file": str(path), "duration_s": float(data.get("format", {}).get("duration", 0) or 0),
           "size_bytes": int(data.get("format", {}).get("size", 0) or 0), "streams": []}
    for s in data.get("streams", []):
        item = {"type": s.get("codec_type"), "codec": s.get("codec_name")}
        if s.get("codec_type") == "video":
            item.update(width=s.get("width"), height=s.get("height"), fps=s.get("avg_frame_rate"))
        if s.get("codec_type") == "audio":
            item.update(sample_rate=s.get("sample_rate"), channels=s.get("channels"))
        out["streams"].append(item)
    return out


# ---------------------------------------------------------------- file locks / fingerprints
def expand(pdir, patterns):
    files = []
    for pat in patterns:
        matches = sorted(glob.glob(str(pdir / pat)))
        files.extend(os.path.relpath(m, pdir) for m in matches if os.path.isfile(m))
    return files


def csv_planning_digest(path):
    rows = read_csv(path)
    h = hashlib.sha256()
    for r in rows:
        keep = [(k, v) for k, v in sorted(r.items()) if k not in SHOT_MUTABLE_COLS]
        h.update(json.dumps(keep, ensure_ascii=False).encode("utf-8"))
    return h.hexdigest()


def fingerprint(pdir, rel):
    path = pdir / rel
    mode = FILE_LOCK_MODE.get(os.path.basename(rel), "full")
    if mode == "csv-planning":
        return {"mode": mode, "sha256": csv_planning_digest(path)}
    return {"mode": mode, "sha256": sha256_file(path), "bytes": path.stat().st_size}


def lock_ok(pdir, rel, rec):
    path = pdir / rel
    if not path.exists():
        return False
    if rec["mode"] == "csv-planning":
        return csv_planning_digest(path) == rec["sha256"]
    if rec["mode"] == "prefix":
        data = path.read_bytes()
        return len(data) >= rec["bytes"] and hashlib.sha256(data[:rec["bytes"]]).hexdigest() == rec["sha256"]
    return sha256_file(path) == rec["sha256"]


def fingerprints(pdir, gate):
    rels = expand(pdir, gate_def(gate)[3])
    return {r: fingerprint(pdir, r) for r in rels}


def deliverable_problems(pdir, gate):
    """Reasons a gate cannot be submitted."""
    problems = []
    for pat in gate_def(gate)[3]:
        matches = expand(pdir, [pat])
        if not matches:
            problems.append("MISSING_DELIVERABLE: %s" % pat)
            continue
        for rel in matches:
            p = pdir / rel
            if rel.endswith((".md", ".csv")):
                text = p.read_text(encoding="utf-8")
                if PLACEHOLDER in text:
                    problems.append("PLACEHOLDER_REMAINS: %s contains %s" % (rel, PLACEHOLDER))
                if rel.endswith(".csv") and len(read_csv(p)) == 0:
                    problems.append("EMPTY_CSV: %s has no data rows" % rel)
            elif rel.endswith((".mp4", ".mov", ".webm")):
                if has_ffprobe():
                    try:
                        info = probe(p)
                        if info["duration_s"] <= 0 or not any(s["type"] == "video" for s in info["streams"]):
                            problems.append("INVALID_MEDIA: %s has no video stream/duration" % rel)
                    except StudioError as e:
                        problems.append("INVALID_MEDIA: %s (%s)" % (rel, e))
    return problems


# ---------------------------------------------------------------- state logic
def gate_state(st, gid):
    return st["gates"][gid]["state"]


def prev_gate(gid):
    i = GATE_IDS.index(gid)
    return GATE_IDS[i - 1] if i else None


def require_prev_approved(st, gid):
    pg = prev_gate(gid)
    if pg and gate_state(st, pg) != "APPROVED":
        raise StudioError("ORDER_VIOLATION: %s must be APPROVED before %s (now %s)" % (pg, gid, gate_state(st, pg)))


def new_status(pid, title, args):
    gates = {}
    for gid, en, ar, dels in GATES:
        gates[gid] = {"name": en, "name_ar": ar, "deliverables": dels, "state": "NOT_STARTED", "version": 0,
                      "submitted_at": None, "submitted_fingerprints": {}, "approved_by": None, "approved_at": None,
                      "locked": {}, "history": []}
    stages = {s[0]: {"state": "NOT_STARTED", "owner": s[4], "requires_gate": s[1], "requires_stages": s[2],
                     "produces_gate": s[3], "started_at": None, "completed_at": None, "note": ""} for s in STAGES}
    return {
        "schema_version": SCHEMA_VERSION, "project_id": pid, "title": title, "language": args.language,
        "runtime_minutes": args.runtime_minutes, "created_at": now(), "updated_at": now(),
        "current_stage": None,
        "facts": {"concept_approved": bool(args.concept_approved), "screenplay_approved": False,
                  "visual_identity_approved": False, "budget_approved": False, "production_tools_approved": False},
        "gates": gates, "stages": stages, "tasks": [], "blockers": [],
        "budget": {"currency": "USD", "approved_total": None, "approved_by": None,
                   "rates": {"image_job": None, "video_second": None, "speech_minute": None,
                             "images_per_shot": 2, "video_attempts_per_shot": 2, "retry_allowance": 0.25},
                   "warn_at_fraction": 0.8},
        "adaptive_retry": {"strategy_change_after_consecutive_failures": 2, "hard_cap": None},
        "authorizations": [], "reviews": [], "delivery_spec": None,
        "adapters": {},
    }


def stage_check_startable(st, name):
    s = STAGE_BY[name]
    if s[1] and gate_state(st, s[1]) != "APPROVED":
        raise StudioError("GATE_REQUIRED: stage '%s' needs %s APPROVED (now %s)" % (name, s[1], gate_state(st, s[1])))
    for r in s[2]:
        if st["stages"][r]["state"] != "DONE":
            raise StudioError("STAGE_DEPENDENCY: stage '%s' needs stage '%s' DONE (now %s)"
                              % (name, r, st["stages"][r]["state"]))


def log_gate(pdir, gid, event, by, note, files=None, version=None):
    lines = ["## %s — %s — %s" % (now(), gid, event), "- By: %s" % by]
    if version is not None:
        lines.append("- Version: %s" % version)
    if note:
        lines.append("- Note: %s" % note)
    for rel, rec in (files or {}).items():
        lines.append("- File: `%s` sha256=%s (%s)" % (rel, rec["sha256"][:16], rec["mode"]))
    append_text(pdir / "APPROVAL_LOG.md", "\n".join(lines) + "\n\n")


def archive_version(pdir, gid, g):
    dest = pdir / "versions" / ("%s_v%d" % (gid, g["version"]))
    n = 1
    while dest.exists():
        n += 1
        dest = pdir / "versions" / ("%s_v%d_%d" % (gid, g["version"], n))
    for rel in g["locked"]:
        if (pdir / rel).exists():
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(pdir / rel, dest / rel)
    return dest


# ---------------------------------------------------------------- commands
def cmd_init(args):
    pid = args.id
    if not re.match(ID_RE["project"], pid):
        raise StudioError("Invalid project id %r (UPPERCASE, digits, hyphens)" % pid)
    pdir = projects_dir(args) / pid
    if pdir.exists():
        raise StudioError("Project already exists: %s (never overwritten)" % pid)
    for d in ["reviews", "prompts/images", "prompts/video", "prompts/audio", "prompts/storyboard", "media/images",
              "media/video", "media/audio", "media/proof_of_concept", "media/rough_cut", "media/final", "versions",
              "exports"]:
        (pdir / d).mkdir(parents=True)
        (pdir / d / ".gitkeep").write_text("")
    facts = ["- Concept: %s" % ("APPROVED by client (stated in brief; informal, not a gate)" if args.concept_approved
                               else "not approved"),
             "- Screenplay: NOT approved", "- Visual identity: NOT approved", "- Budget: NOT approved",
             "- Production tools: NOT verified/approved"] + ["- Client note: %s" % n for n in (args.note or [])]
    subs = {"PROJECT_ID": pid, "TITLE": args.title, "GENRE": args.genre, "LANGUAGE": args.language,
            "RUNTIME_MINUTES": str(args.runtime_minutes), "PROTAGONIST": args.protagonist, "SETTING": args.setting,
            "STYLE": args.style, "DELIVERABLE": args.deliverable, "DATE": today(), "FACTS": "\n".join(facts)}
    for rel in [f for f in ROOT_FILES if f != "PROJECT_STATUS.json"] + EXTRA_FILES:
        src = TEMPLATES / TEMPLATE_MAP.get(rel, rel)
        text = src.read_text(encoding="utf-8")
        for k, v in subs.items():
            text = text.replace("{{%s}}" % k, v)
        (pdir / rel).write_text(text, encoding="utf-8")
    save(pdir, new_status(pid, args.title, args))
    print("Project created: %s" % pdir)
    return 0


def cmd_status(args):
    pdir = project_dir(args)
    st = load(pdir)
    if args.json:
        print(json.dumps(st, indent=2, ensure_ascii=False))
        return 0
    print("== حالة الإنتاج: %s — %s ==" % (st["project_id"], st["title"]))
    print("المرحلة الحالية: %s" % (st["current_stage"] or "لم يبدأ الإنتاج بعد"))
    print("\nالبوابات:")
    for gid in GATE_IDS:
        g = st["gates"][gid]
        extra = " (v%d، اعتمدها: %s)" % (g["version"], g["approved_by"]) if g["state"] == "APPROVED" else ""
        print("  %s  %-24s %s%s" % (gid, AR.get(g["state"], g["state"]), g["name_ar"], extra))
    print("\nالمراحل:")
    for name in STAGE_NAMES:
        s = st["stages"][name]
        if s["state"] != "NOT_STARTED":
            print("  %-20s %s  (المسؤول: %s)" % (name, AR.get(s["state"], s["state"]), s["owner"]))
    blocked = [(n, s) for n, s in st["stages"].items() if s["state"].startswith("BLOCKED")]
    if blocked or st["blockers"]:
        print("\nعوائق:")
        for n, s in blocked:
            print("  - %s: %s %s" % (n, AR[s["state"]], s["note"]))
        for b in st["blockers"]:
            print("  - %s" % b)
    t = st["tasks"]
    if t:
        done = sum(1 for x in t if x["status"] == "DONE")
        print("\nالمهام: %d/%d مكتملة" % (done, len(t)))
    est, act = cost_totals(pdir)
    ap = st["budget"]["approved_total"]
    print("\nالتكلفة: تقديرية %.2f | فعلية %.2f | الميزانية المعتمدة: %s %s" % (
        est, act, ap if ap is not None else "غير معتمدة", st["budget"]["currency"]))
    print("\nالحقائق: " + "، ".join("%s=%s" % (k, v) for k, v in st["facts"].items()))
    return 0


def cmd_pending(args):
    pdir = project_dir(args)
    st = load(pdir)
    found = False
    for gid in GATE_IDS:
        g = st["gates"][gid]
        if g["state"] in ("PENDING_APPROVAL",):
            found = True
            print("⏳ %s — %s — بانتظار قرارك (إرسال: %s)" % (gid, g["name_ar"], g["submitted_at"]))
            for rel, rec in g["submitted_fingerprints"].items():
                print("   - %s  sha256:%s" % (rel, rec["sha256"][:12]))
            print("   الأوامر: /studio-approve %s  |  /studio-revise %s <ملاحظات>  |  /studio-reject %s <سبب>" % (gid, gid, gid))
        elif g["state"] in ("REVISION_REQUESTED", "REJECTED", "STALE"):
            print("⚠ %s — %s" % (gid, AR[g["state"]]))
    if not found:
        print("لا توجد بوابات بانتظار الموافقة حالياً.")
    return 0


def cmd_next(args):
    pdir = project_dir(args)
    st = load(pdir)
    for name in STAGE_NAMES:
        s = STAGE_BY[name]
        sst = st["stages"][name]
        if sst["state"] == "DONE":
            continue
        print("المرحلة التالية: %s (الحالة: %s) — الوكيل: %s — المهارات: %s" % (name, AR[sst["state"]], s[4], s[5]))
        try:
            stage_check_startable(st, name)
            print("جاهزة للبدء: python3 studio/studio.py stage start %s -p %s" % (name, st["project_id"]))
        except StudioError as e:
            print("ممنوعة حالياً: %s" % e)
            if s[1] and gate_state(st, s[1]) == "PENDING_APPROVAL":
                print("الإجراء: اعرض طلب الموافقة على %s (/studio-approvals)." % s[1])
            elif s[1]:
                print("الإجراء: أكمل المراحل السابقة ثم قدّم %s للموافقة. يمكن التحضير كمسودات فقط." % s[1])
        gate = s[3]
        if gate and sst["state"] == "DONE":
            pass
        return 0
    print("كل المراحل مكتملة.")
    return 0


def cmd_stage(args):
    pdir = project_dir(args)
    st = load(pdir)
    name = args.name
    if name not in STAGE_BY:
        raise StudioError("Unknown stage %s (%s)" % (name, ", ".join(STAGE_NAMES)))
    s = st["stages"][name]
    if args.action == "start":
        if s["state"] in ("DONE",):
            raise StudioError("Stage already DONE")
        stage_check_startable(st, name)
        s["state"], s["started_at"], s["note"] = "IN_PROGRESS", s["started_at"] or now(), ""
        st["current_stage"] = name
        g = STAGE_BY[name][3]
        if g and gate_state(st, g) == "NOT_STARTED":
            st["gates"][g]["state"] = "PREPARING"
    elif args.action == "done":
        if s["state"] != "IN_PROGRESS":
            raise StudioError("Stage must be IN_PROGRESS to be marked DONE (now %s)" % s["state"])
        s["state"], s["completed_at"] = "DONE", now()
    elif args.action == "block":
        s["state"] = "BLOCKED_EXTERNAL_TOOL" if args.external_tool else "BLOCKED"
        s["note"] = args.reason or ""
    elif args.action == "unblock":
        if not s["state"].startswith("BLOCKED"):
            raise StudioError("Stage is not blocked")
        s["state"], s["note"] = "IN_PROGRESS", ""
    save(pdir, st)
    print("%s -> %s" % (name, s["state"]))
    return 0


def cmd_task(args):
    pdir = project_dir(args)
    st = load(pdir)
    tasks = st["tasks"]
    if args.action == "add":
        if args.stage not in STAGE_BY:
            raise StudioError("Unknown stage %s" % args.stage)
        deps = [d for d in (args.depends or "").split(",") if d]
        ids = [t["id"] for t in tasks]
        for d in deps:
            if d not in ids:
                raise StudioError("Unknown dependency %s" % d)
        if not args.owner:
            raise StudioError("Every task needs an owner")
        tid = next_id("TK", ids, 4)
        tasks.append({"id": tid, "title": args.title, "stage": args.stage, "owner": args.owner,
                      "status": "TODO", "depends_on": deps, "created_at": now()})
        print(tid)
    elif args.action == "update":
        t = next((x for x in tasks if x["id"] == args.id), None)
        if not t:
            raise StudioError("Unknown task %s" % args.id)
        if args.status not in TASK_STATES:
            raise StudioError("Status must be one of %s" % TASK_STATES)
        if args.status in ("IN_PROGRESS", "DONE"):
            open_deps = [d for d in t["depends_on"] if next(x for x in tasks if x["id"] == d)["status"] != "DONE"]
            if open_deps:
                raise StudioError("DEPENDENCY_OPEN: %s waits for %s" % (t["id"], ", ".join(open_deps)))
        t["status"] = args.status
        print("%s -> %s" % (t["id"], t["status"]))
    else:
        for t in tasks:
            print("%s  %-11s %-18s %-30s deps=%s  %s" % (t["id"], t["status"], t["stage"], t["owner"],
                                                       ",".join(t["depends_on"]) or "-", t["title"]))
    save(pdir, st)
    return 0


def cmd_submit(args):
    pdir = project_dir(args)
    st = load(pdir)
    gid = args.gate.upper()
    gate_def(gid)
    g = st["gates"][gid]
    if g["state"] in ("PENDING_APPROVAL", "APPROVED"):
        raise StudioError("STATE: %s is already %s" % (gid, g["state"]))
    require_prev_approved(st, gid)
    problems = deliverable_problems(pdir, gid)
    for fn in SUBMIT_CHECKS:
        problems.extend(fn(pdir, st, gid))
    if problems:
        raise StudioError("NOT_SUBMITTABLE:\n  " + "\n  ".join(problems))
    g["submitted_fingerprints"] = fingerprints(pdir, gid)
    g["submitted_at"] = now()
    g["state"] = "PENDING_APPROVAL"
    g["history"].append({"at": now(), "event": "SUBMITTED"})
    log_gate(pdir, gid, "SUBMITTED", args.by or "executive-producer", args.note, g["submitted_fingerprints"])
    save(pdir, st)
    print("%s submitted: awaiting human approval" % gid)
    return 0


def cmd_approve(args):
    pdir = project_dir(args)
    st = load(pdir)
    gid = args.gate.upper()
    gate_def(gid)
    g = st["gates"][gid]
    by_policy = bool(getattr(args, "policy", False))
    if by_policy:
        args.by = autonomy.POLICY_APPROVER
    elif not args.by or not args.by.strip():
        raise StudioError("--by <human name> is required: approvals come from a human (or --policy for policy-eligible gates)")
    elif args.by.strip().upper().startswith("POLICY"):
        raise StudioError("--by POLICY... is reserved; use --policy so the engine checks the approval policy")
    if g["state"] != "PENDING_APPROVAL":
        raise StudioError("STATE: %s is %s; only PENDING_APPROVAL can be approved (submit first)" % (gid, g["state"]))
    require_prev_approved(st, gid)
    problems = autonomy.approval_problems(pdir, st, gid, by_policy)
    if problems:
        raise StudioError("APPROVAL_REFUSED:\n  " + "\n  ".join(problems))
    if by_policy:
        args.note = "%s [policy sha %s]" % (args.note or "auto-approval: all conditions met", autonomy.policy_sha(pdir))
    changed = [rel for rel, rec in g["submitted_fingerprints"].items()
               if not (pdir / rel).exists() or fingerprint(pdir, rel) != rec]
    if changed:
        raise StudioError("CHANGED_SINCE_SUBMIT: %s modified after submission; resubmit for review" % ", ".join(changed))
    g["locked"] = g["submitted_fingerprints"]
    g["version"] += 1
    g["state"], g["approved_by"], g["approved_at"] = "APPROVED", args.by, now()
    g["history"].append({"at": now(), "event": "APPROVED", "by": args.by, "note": args.note})
    for name, s in st["stages"].items():
        if s["produces_gate"] == gid and s["state"] in ("IN_PROGRESS", "NOT_STARTED"):
            s["state"], s["completed_at"] = "DONE", now()
    if gid == "GATE-03":
        st["facts"]["screenplay_approved"] = True
    if gid == "GATE-04":
        st["facts"]["visual_identity_approved"] = True
    if gid == "GATE-07":
        st["facts"]["budget_approved"] = True
    # downstream STALE gates are not auto-restored: they must be re-submitted
    log_gate(pdir, gid, "APPROVED", args.by, args.note, g["locked"], g["version"])
    save(pdir, st)
    print("%s APPROVED by %s (v%d) — files locked" % (gid, args.by, g["version"]))
    return 0


def cmd_revise(args):
    pdir = project_dir(args)
    st = load(pdir)
    gid = args.gate.upper()
    gate_def(gid)
    g = st["gates"][gid]
    if not args.by or not args.note:
        raise StudioError("--by and --note are required")
    if g["state"] not in ("PENDING_APPROVAL", "APPROVED"):
        raise StudioError("STATE: %s is %s; nothing to revise" % (gid, g["state"]))
    reopened = g["state"] == "APPROVED"
    if reopened:
        dest = archive_version(pdir, gid, g)
        print("Previous approved version archived in %s" % os.path.relpath(dest, pdir))
        g["locked"] = {}
        for later in GATE_IDS[GATE_IDS.index(gid) + 1:]:
            if st["gates"][later]["state"] in ("APPROVED", "PENDING_APPROVAL"):
                st["gates"][later]["state"] = "STALE"
        gi = GATE_IDS.index(gid)
        for name, s in st["stages"].items():
            if s["produces_gate"] == gid and s["state"] == "DONE":
                s["state"] = "IN_PROGRESS"
            elif s["requires_gate"] and GATE_IDS.index(s["requires_gate"]) >= gi and s["state"] in ("IN_PROGRESS", "DONE"):
                s["state"], s["note"] = "BLOCKED", "UPSTREAM_REOPENED:%s (restart the stage after re-approval)" % gid
    g["state"] = "REVISION_REQUESTED"
    g["history"].append({"at": now(), "event": "REVISION_REQUESTED", "by": args.by, "note": args.note})
    log_gate(pdir, gid, "REVISION_REQUESTED" + (" (reopened approved gate)" if reopened else ""), args.by, args.note)
    save(pdir, st)
    print("%s -> REVISION_REQUESTED" % gid)
    return 0


def cmd_reject(args):
    pdir = project_dir(args)
    st = load(pdir)
    gid = args.gate.upper()
    gate_def(gid)
    g = st["gates"][gid]
    if not args.by or not args.note:
        raise StudioError("--by and --note are required")
    if g["state"] != "PENDING_APPROVAL":
        raise StudioError("STATE: only PENDING_APPROVAL gates can be rejected (now %s)" % g["state"])
    g["state"] = "REJECTED"
    g["history"].append({"at": now(), "event": "REJECTED", "by": args.by, "note": args.note})
    log_gate(pdir, gid, "REJECTED", args.by, args.note)
    save(pdir, st)
    print("%s -> REJECTED" % gid)
    return 0


def cmd_log(args):
    pdir = project_dir(args)
    st = load(pdir)
    if args.kind not in ("PLANNED", "ACTUAL"):
        raise StudioError("--kind must be PLANNED or ACTUAL")
    if args.stage not in STAGE_BY:
        raise StudioError("Unknown stage %s" % args.stage)
    if args.kind == "ACTUAL" and not args.status:
        raise StudioError("ACTUAL entries need --status (SUCCESS/FAILED/...) — record what really happened")
    rows = read_csv(pdir / "PRODUCTION_LOG.csv")
    lid = next_id("LG", [r["log_id"] for r in rows], 4)
    append_csv(pdir / "PRODUCTION_LOG.csv", CSV_HEADERS["PRODUCTION_LOG.csv"],
               {"log_id": lid, "timestamp": now(), "kind": args.kind, "task_id": args.task or "", "stage": args.stage,
                "owner": args.owner or "", "action": args.action, "status": args.status or "PLANNED",
                "tool": args.tool or "", "cost_usd": args.cost or "", "details": args.details or ""})
    print(lid)
    return 0


def cmd_changelog(args):
    pdir = project_dir(args)
    if not (pdir / args.file).exists():
        raise StudioError("File not in project: %s" % args.file)
    append_text(pdir / "CHANGE_LOG.md", "## %s — %s\n- By: %s\n- Version: %s\n- Summary: %s\n\n" % (
        now(), args.file, args.by, args.version or "-", args.summary))
    print("logged")
    return 0


def cost_totals(pdir):
    est = act = 0.0
    for r in read_csv(pdir / "COST_TRACKER.csv"):
        try:
            amt = float(r["amount"] or 0)
        except ValueError:
            continue
        if r["kind"] == "ESTIMATE":
            est += amt
        elif r["kind"] == "ACTUAL":
            act += amt
    return est, act


def spend(st, auth_id, amount):
    """Consume `amount` from authorization `auth_id` (mutates st; caller saves). Refuses overspend."""
    if not auth_id:
        raise StudioError("AUTHORIZATION_REQUIRED: spending needs --authorization AU-xxxx (see `cost authorize`)")
    a = next((x for x in st["authorizations"] if x["id"] == auth_id), None)
    if not a:
        raise StudioError("UNKNOWN_AUTHORIZATION: %s" % auth_id)
    if a["consumed"] + amount > a["amount"] + 1e-9:
        raise StudioError("AUTHORIZATION_EXCEEDED: %s has %.2f left, need %.2f; ask the human for a new authorization"
                          % (auth_id, a["amount"] - a["consumed"], amount))
    a["consumed"] = round(a["consumed"] + amount, 4)
    return auth_id


def cmd_cost(args):
    pdir = project_dir(args)
    st = load(pdir)
    b = st["budget"]
    if args.action == "set-budget":
        if not args.by:
            raise StudioError("--by <human> required")
        b["approved_total"], b["approved_by"] = float(args.amount), args.by
        save(pdir, st)
        print("Budget set to %s %s by %s" % (args.amount, b["currency"], args.by))
    elif args.action == "authorize":
        # Cost authorization is NOT artistic approval and is never implied by a gate approval.
        if not args.by or not args.amount or not args.scope:
            raise StudioError("cost authorize needs --amount --scope --by (a human decision)")
        amt = float(args.amount)
        total = sum(a["amount"] for a in st["authorizations"]) + amt
        if b["approved_total"] is not None and total > b["approved_total"]:
            raise StudioError("OVER_APPROVED_BUDGET: authorizations %.2f would exceed approved budget %.2f; "
                              "the human must raise the budget first (cost set-budget)" % (total, b["approved_total"]))
        aid = next_id("AU", [a["id"] for a in st["authorizations"]], 4)
        st["authorizations"].append({"id": aid, "amount": amt, "consumed": 0.0, "scope": args.scope, "by": args.by,
                                     "at": now(), "phase": args.phase or "any"})
        log_gate(pdir, "COST", "COST_AUTHORIZED %s (%.2f %s; spending permission only, NOT artistic approval)" % (aid, amt, b["currency"]),
                 args.by, args.scope)
        save(pdir, st)
        print("%s authorized: %.2f %s for %s" % (aid, amt, b["currency"], args.scope))
    elif args.action == "add":
        if args.kind not in ("ESTIMATE", "ACTUAL"):
            raise StudioError("--kind ESTIMATE|ACTUAL")
        amount = round(float(args.units) * float(args.unit_cost), 2)
        auth_id = ""
        if args.kind == "ACTUAL" and amount > 0:
            auth_id = spend(st, args.authorization, amount)
            save(pdir, st)
        rows = read_csv(pdir / "COST_TRACKER.csv")
        cid = next_id("CE", [r["entry_id"] for r in rows], 4)
        append_csv(pdir / "COST_TRACKER.csv", CSV_HEADERS["COST_TRACKER.csv"],
                   {"entry_id": cid, "date": today(), "kind": args.kind, "stage": args.stage, "item": args.item,
                    "units": args.units, "unit_cost": args.unit_cost, "amount": amount, "currency": b["currency"],
                    "authorized_by": auth_id, "notes": args.notes or ""})
        print(cid)
    elif args.action == "summary":
        est, act = cost_totals(pdir)
        print("ESTIMATE total: %.2f | ACTUAL total: %.2f | approved budget: %s %s" % (
            est, act, b["approved_total"], b["currency"]))
        if b["approved_total"]:
            frac = act / b["approved_total"]
            print("Budget used: %.0f%%%s" % (frac * 100, "  WARNING >= warn threshold" if frac >= b["warn_at_fraction"] else ""))
    elif args.action == "estimate":
        shots = read_csv(pdir / "SHOT_LIST.csv")
        secs = sum(float(r["duration_s"] or 0) for r in shots)
        r = b["rates"]
        ips, vat, ra = r["images_per_shot"], r["video_attempts_per_shot"], r["retry_allowance"]
        jobs_img = len(shots) * ips * (1 + ra)
        sec_vid = secs * vat * (1 + ra)
        print("Shots: %d | planned seconds: %.1f | image jobs (incl. retries): %.1f | video seconds (incl. retries): %.1f"
              % (len(shots), secs, jobs_img, sec_vid))
        missing = [k for k in ("image_job", "video_second", "speech_minute") if r[k] is None]
        if missing:
            print("Rates not configured (%s): cannot produce a money estimate. Set real prices from the chosen "
                  "tool's pricing in PROJECT_STATUS.json budget.rates (via human instruction). Not guessing."
                  % ", ".join(missing))
            return 0
        lines = [("Images", jobs_img, r["image_job"]), ("Video seconds", sec_vid, r["video_second"])]
        total = sum(u * c for _, u, c in lines)
        for n, u, c in lines:
            print("%-14s %.1f x %.4f = %.2f" % (n, u, c, u * c))
        print("ESTIMATE total (excl. speech): %.2f %s" % (total, b["currency"]))
        if args.write:
            for n, u, c in lines:
                rows = read_csv(pdir / "COST_TRACKER.csv")
                append_csv(pdir / "COST_TRACKER.csv", CSV_HEADERS["COST_TRACKER.csv"], {
                    "entry_id": next_id("CE", [x["entry_id"] for x in rows], 4), "date": today(), "kind": "ESTIMATE",
                    "stage": "production_planning", "item": n, "units": round(u, 2), "unit_cost": c,
                    "amount": round(u * c, 2), "currency": b["currency"]})
    return 0


def cmd_adapters(args):
    spec = json.loads(ADAPTERS_JSON.read_text(encoding="utf-8"))["adapters"]
    result = {}
    for name, a in spec.items():
        d = a["detect"]
        bins = {b: bool(shutil.which(b)) for b in d.get("bins", [])}
        envs = {e: (e in os.environ) for e in d.get("env", [])}  # names only, never values
        if d.get("always") or (bins and all(bins.values())):
            status = "AVAILABLE"
        elif any(envs.values()):
            status = "CREDENTIAL_ENV_PRESENT_UNVERIFIED"
        elif bins and any(bins.values()):
            status = "PARTIAL"
        else:
            status = "BLOCKED_EXTERNAL_TOOL" if a["kind"] != "local" else "MISSING_LOCAL_TOOL"
        result[name] = {"status": status, "paid": a["paid"], "binaries": bins, "env_names_present": [k for k, v in envs.items() if v]}
    for name, r in result.items():
        print("%-18s %-34s paid=%-5s bins=%s env=%s" % (name, r["status"], r["paid"], r["binaries"] or "-", r["env_names_present"] or "-"))
    print("\nNote: connector/MCP tools in a Claude session cannot be detected here; inspect the session's tool list "
          "and ask the human before any paid job.")
    if True:
        try:
            pdir = project_dir(args)
            st = load(pdir)
            st["adapters"] = {k: {"status": v["status"], "checked_at": now()} for k, v in result.items()}
            save(pdir, st)
        except StudioError:
            pass
    return 0


def cmd_probe(args):
    print(json.dumps(probe(args.file), indent=2))
    return 0


def cmd_validate(args):
    pdir = project_dir(args)
    errors, warns = [], []
    try:
        st = load(pdir)
    except Exception as e:  # noqa
        print("ERROR INVALID_STATUS_JSON: %s" % e)
        return 1
    if st.get("schema_version") != SCHEMA_VERSION:
        errors.append("SCHEMA_VERSION mismatch")
    if st.get("project_id") != pdir.name:
        errors.append("PROJECT_ID mismatch (%s vs folder %s)" % (st.get("project_id"), pdir.name))
    for rel in ROOT_FILES + EXTRA_FILES:
        if not (pdir / rel).exists():
            errors.append("MISSING_FILE: %s" % rel)
    for g in GATE_IDS:
        if g not in st["gates"]:
            errors.append("MISSING_GATE: %s" % g)
    if errors:
        return report(errors, warns)
    # locks
    for gid in GATE_IDS:
        g = st["gates"][gid]
        if g["state"] == "APPROVED":
            if not g["locked"]:
                errors.append("NO_LOCK: %s approved without locked fingerprints" % gid)
            for rel, rec in g["locked"].items():
                if not lock_ok(pdir, rel, rec):
                    errors.append("LOCKED_FILE_MODIFIED: %s (approved in %s) changed without `revise`" % (rel, gid))
    # order
    for i, gid in enumerate(GATE_IDS[1:], 1):
        if st["gates"][gid]["state"] in ("APPROVED", "PENDING_APPROVAL") and st["gates"][GATE_IDS[i - 1]]["state"] != "APPROVED":
            errors.append("ORDER_VIOLATION: %s is %s but %s is %s" % (gid, st["gates"][gid]["state"], GATE_IDS[i - 1],
                                                                       st["gates"][GATE_IDS[i - 1]]["state"]))
    for name, s in st["stages"].items():
        if s["state"] not in STAGE_STATES:
            errors.append("BAD_STAGE_STATE: %s=%s" % (name, s["state"]))
        rg = s["requires_gate"]
        if rg and s["state"] in ("IN_PROGRESS", "DONE") and st["gates"][rg]["state"] != "APPROVED":
            errors.append("STAGE_WITHOUT_GATE: %s is %s but %s is %s" % (name, s["state"], rg, st["gates"][rg]["state"]))
    # csv
    for fn, header in CSV_HEADERS.items():
        with open(pdir / fn, newline="", encoding="utf-8") as f:
            first = next(csv.reader(f), [])
        if ",".join(first) != header:
            errors.append("CSV_HEADER: %s header differs from template" % fn)
    if not errors:
        seen = set()
        for r in read_csv(pdir / "SHOT_LIST.csv"):
            if not re.match(ID_RE["shot"], r["shot_id"]):
                errors.append("BAD_SHOT_ID: %r" % r["shot_id"])
            if r["shot_id"] in seen:
                errors.append("DUP_SHOT_ID: %s" % r["shot_id"])
            seen.add(r["shot_id"])
            if not re.match(ID_RE["scene"], r["scene_id"]):
                errors.append("BAD_SCENE_ID: %s (%s)" % (r["scene_id"], r["shot_id"]))
            if r["status"] not in SHOT_STATES:
                errors.append("BAD_SHOT_STATUS: %s=%s" % (r["shot_id"], r["status"]))
            try:
                if not 0 < float(r["duration_s"]) <= 15:
                    errors.append("BAD_DURATION: %s" % r["shot_id"])
            except ValueError:
                errors.append("BAD_DURATION: %s" % r["shot_id"])
        for r in read_csv(pdir / "COST_TRACKER.csv"):
            if r["kind"] not in ("ESTIMATE", "ACTUAL"):
                errors.append("BAD_COST_KIND: %s" % r["entry_id"])
            if r["kind"] == "ACTUAL" and float(r["amount"] or 0) > 0 and not r["authorized_by"]:
                errors.append("UNAUTHORIZED_SPEND: %s" % r["entry_id"])
            if r["kind"] == "ACTUAL" and float(r["amount"] or 0) > 0 and r["authorized_by"] not in {a["id"] for a in st["authorizations"]}:
                errors.append("UNKNOWN_AUTHORIZATION: %s cites %r" % (r["entry_id"], r["authorized_by"]))
        for r in read_csv(pdir / "PRODUCTION_LOG.csv"):
            if r["kind"] not in ("PLANNED", "ACTUAL"):
                errors.append("BAD_LOG_KIND: %s" % r["log_id"])
    # tasks
    ids = {t["id"] for t in st["tasks"]}
    for t in st["tasks"]:
        if not t.get("owner"):
            errors.append("TASK_NO_OWNER: %s" % t["id"])
        for d in t["depends_on"]:
            if d not in ids:
                errors.append("TASK_BAD_DEP: %s -> %s" % (t["id"], d))
            elif t["status"] in ("IN_PROGRESS", "DONE") and next(x for x in st["tasks"] if x["id"] == d)["status"] != "DONE":
                errors.append("TASK_DEP_OPEN: %s %s before %s DONE" % (t["id"], t["status"], d))
    # secrets
    for p in pdir.rglob("*"):
        if p.is_file() and p.suffix in (".md", ".csv", ".json", ".txt") and "versions" not in p.parts:
            text = p.read_text(encoding="utf-8", errors="ignore")
            for rx in SECRET_RES:
                if rx.search(text):
                    errors.append("POSSIBLE_SECRET: %s matches %s" % (os.path.relpath(p, pdir), rx.pattern[:20]))
                    break
    if not has_ffprobe():
        warns.append("ffprobe missing: media cannot be verified")
    for fn in VALIDATE_CHECKS:
        e, w = fn(pdir, st)
        errors.extend(e)
        warns.extend(w)
    return report(errors, warns)


def report(errors, warns):
    for w in warns:
        print("WARN  %s" % w)
    for e in errors:
        print("ERROR %s" % e)
    print("VALID" if not errors else "INVALID (%d errors)" % len(errors))
    return 1 if errors else 0


def cmd_export(args):
    pdir = project_dir(args)
    st = load(pdir)
    base = pdir / "exports" / ("export_" + datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ"))
    n = 1
    dest = base
    while dest.exists():
        n += 1
        dest = Path(str(base) + "_%d" % n)
    manifest = []
    locked = {}
    for gid in GATE_IDS:
        for rel in st["gates"][gid]["locked"]:
            locked[rel] = gid
    files = [f for f in ROOT_FILES + EXTRA_FILES if (pdir / f).exists()] + expand(pdir, ["media/proof_of_concept/*",
             "media/rough_cut/*", "media/final/*"])
    for rel in dict.fromkeys(files):
        if os.path.basename(rel) == ".gitkeep":
            continue
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pdir / rel, dest / rel)
        manifest.append({"file": rel, "sha256": sha256_file(pdir / rel),
                         "status": ("APPROVED:" + locked[rel]) if rel in locked else "DRAFT"})
    (dest / "MANIFEST.json").write_text(json.dumps({"project_id": st["project_id"], "exported_at": now(),
                                                    "files": manifest}, indent=2), encoding="utf-8")
    approved = sum(1 for m in manifest if m["status"].startswith("APPROVED"))
    print("Export: %s  (%d files, %d approved, %d draft)" % (dest, len(manifest), approved, len(manifest) - approved))
    return 0


# ---------------------------------------------------------------- CLI
def build_parser():
    p = argparse.ArgumentParser(prog="studio.py", description="AI Film Studio workflow engine")
    p.add_argument("--projects-dir", help="override projects directory (or env STUDIO_PROJECTS_DIR)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, **kw):
        sp = sub.add_parser(name, **kw)
        sp.add_argument("-p", "--project")
        sp.set_defaults(fn=fn)
        return sp

    sp = add("init", cmd_init)
    sp.add_argument("--id", required=True)
    sp.add_argument("--title", required=True)
    sp.add_argument("--genre", default="[[TBD]]")
    sp.add_argument("--language", default="English")
    sp.add_argument("--runtime-minutes", type=float, default=5)
    sp.add_argument("--protagonist", default="[[TBD]]")
    sp.add_argument("--setting", default="[[TBD]]")
    sp.add_argument("--style", default="Photorealistic, cinematic, live-action appearance")
    sp.add_argument("--deliverable", default="One complete cinematic film")
    sp.add_argument("--concept-approved", action="store_true")
    sp.add_argument("--note", action="append")
    sp = add("status", cmd_status)
    sp.add_argument("--json", action="store_true")
    add("pending", cmd_pending)
    add("next", cmd_next)
    sp = add("stage", cmd_stage)
    sp.add_argument("action", choices=["start", "done", "block", "unblock"])
    sp.add_argument("name")
    sp.add_argument("--external-tool", action="store_true")
    sp.add_argument("--reason")
    sp = add("task", cmd_task)
    sp.add_argument("action", choices=["add", "update", "list"])
    sp.add_argument("--title")
    sp.add_argument("--stage")
    sp.add_argument("--owner")
    sp.add_argument("--depends")
    sp.add_argument("--id")
    sp.add_argument("--status")
    for name, fn in (("submit", cmd_submit), ("approve", cmd_approve), ("revise", cmd_revise), ("reject", cmd_reject)):
        sp = add(name, fn)
        sp.add_argument("gate")
        sp.add_argument("--by")
        sp.add_argument("--note")
        if name == "approve":
            sp.add_argument("--policy", action="store_true",
                            help="approve under the project's AUTO_APPROVAL policy (engine checks every condition)")
    sp = add("log", cmd_log)
    sp.add_argument("--kind", required=True)
    sp.add_argument("--stage", required=True)
    sp.add_argument("--action", required=True)
    for a in ("task", "owner", "status", "tool", "cost", "details"):
        sp.add_argument("--" + a)
    sp = add("changelog", cmd_changelog)
    sp.add_argument("--file", required=True)
    sp.add_argument("--summary", required=True)
    sp.add_argument("--by", required=True)
    sp.add_argument("--version")
    sp = add("cost", cmd_cost)
    sp.add_argument("action", choices=["add", "summary", "estimate", "set-budget", "authorize"])
    for a in ("kind", "stage", "item", "units", "unit-cost", "authorization", "notes", "amount", "by", "scope", "phase"):
        sp.add_argument("--" + a)
    sp.add_argument("--write", action="store_true")
    add("adapters", cmd_adapters)
    sp = add("probe", cmd_probe)
    sp.add_argument("file")
    add("validate", cmd_validate)
    add("export", cmd_export)
    ext.register(sys.modules[__name__], add)
    autonomy.register(sys.modules[__name__], ext, add)
    import orchestrator  # noqa: E402  (film controller; imports the core lazily)
    orchestrator.register(sys.modules[__name__], add)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    lockf = None
    if args.fn.__name__ != "cmd_film":
        # one engine command at a time across processes (orchestrator + agents running in parallel):
        # every command is load-modify-save on PROJECT_STATUS.json and CSV logs
        import fcntl
        try:
            base = projects_dir(args)
            base.mkdir(parents=True, exist_ok=True)
            lockf = open(base / ".engine.lock", "a")
            fcntl.flock(lockf, fcntl.LOCK_EX)
        except OSError:
            lockf = None
    try:
        return args.fn(args)
    except StudioError as e:
        print("ERROR %s" % e, file=sys.stderr)
        return 2
    finally:
        if lockf:
            lockf.close()


if __name__ == "__main__":
    sys.exit(main())

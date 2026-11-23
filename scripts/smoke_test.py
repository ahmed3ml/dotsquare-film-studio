#!/usr/bin/env python3
"""Non-destructive smoke test: runs a full MOCK project through all 9 gates in a temp directory.
No network, no paid APIs. Mock documents are labelled MOCK. Media: tiny local test videos generated with
FFmpeg's built-in test source (free, local); if ffmpeg is missing the video gates are reported SKIPPED.
Never touches projects/MUTAH-PILOT."""
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "studio"))
import studio  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="studio_smoke_"))
P = tmp / "projects"
fails = []


def run(*argv, expect=0):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        code = studio.main(["--projects-dir", str(P)] + list(argv))
    ok = code == expect
    print("%s studio %s%s" % ("PASS" if ok else "FAIL", " ".join(argv), "" if ok else "\n" + buf.getvalue()))
    if not ok:
        fails.append(argv)
    return buf.getvalue()


def video(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=24:duration=1",
                    "-pix_fmt", "yuv420p", str(path)], check=True)


import json
crit = json.loads((studio.STUDIO_ROOT / "standards" / "criteria.json").read_text())


def accept(gate, target=None, cset_list=None, files=None, creator="smoke-creator"):
    """Genuine review cycle with MOCK evidence by a different actor (the engine enforces independence)."""
    for cset in (cset_list or crit["gate_reviews"][gate]):
        out = run("review", "open", "--target", target or ("gate:" + gate), "--set", cset, "--creator", creator,
                  *(["--files", files] if files else []), "-p", "MOCK-SMOKE")
        rid = [w for w in out.split() if w.startswith("RC-")][0]
        ev = d / "reviews/evidence/mock.txt"
        ev.parent.mkdir(parents=True, exist_ok=True)
        ev.write_text("MOCK evidence\n")
        v = {"criteria": {c["id"]: {"result": "PASS", "evidence": "MOCK evidence for smoke test %s" % c["id"],
                                    "evidence_files": ["reviews/evidence/mock.txt"]} for c in crit["sets"][cset]}}
        vf = d / ("reviews/verdicts/%s.json" % rid)
        vf.parent.mkdir(parents=True, exist_ok=True)
        vf.write_text(json.dumps(v))
        run("review", "close", rid, "--reviewer", "smoke-reviewer", "--verdict-file", str(vf), "-p", "MOCK-SMOKE")


run("init", "--id", "MOCK-SMOKE", "--title", "Mock Film", "--concept-approved")
d = P / "MOCK-SMOKE"
p = ["-p", "MOCK-SMOKE"]
run("validate", *p)
run("submit", "GATE-01", *p, expect=2)  # placeholders + no review
run("submit", "GATE-02", *p, expect=2)  # order enforced
run("approve", "GATE-01", "--by", "Smoke", *p, expect=2)  # not submitted
docs = {"GATE-01": ["HISTORICAL_BIBLE.md"], "GATE-02": ["STORY_TREATMENT.md"],
        "GATE-03": ["SCREENPLAY.md", "reviews/SCRIPT_REVIEW.md"],
        "GATE-04": ["CHARACTER_BIBLE.md", "ENVIRONMENT_BIBLE.md", "VISUAL_STYLE_GUIDE.md"]}
stage_for = {"GATE-01": "research", "GATE-02": "story", "GATE-03": "script_review", "GATE-04": "visual_development"}
have_ffmpeg = shutil.which("ffmpeg") is not None
for gate in ["GATE-01", "GATE-02", "GATE-03", "GATE-04", "GATE-05", "GATE-06", "GATE-07", "GATE-08", "GATE-09"]:
    n = int(gate[-2:])
    if gate == "GATE-03":
        run("stage", "start", "screenplay", *p)
        (d / "SCREENPLAY.md").write_text("# MOCK SCREENPLAY\nSC-001 mock.\n")
        run("stage", "done", "screenplay", *p)
        run("stage", "start", "script_review", *p)
    elif gate in stage_for:
        run("stage", "start", stage_for[gate], *p)
    for rel in docs.get(gate, []):
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text("# MOCK %s\nContent for smoke test only.\n" % rel)
    if gate == "GATE-05":
        row = ",".join({"shot_id": "SH-001-01", "scene_id": "SC-001", "seq": "1", "description": "mock",
                        "duration_s": "1", "status": "PLANNED", "retries": "0"}.get(c, "")
                       for c in studio.CSV_HEADERS["SHOT_LIST.csv"].split(","))
        (d / "SHOT_LIST.csv").write_text(studio.CSV_HEADERS["SHOT_LIST.csv"] + "\n" + row + "\n")
    if gate == "GATE-06":
        if not have_ffmpeg:
            print("SKIP GATE-06..09: ffmpeg not available for local mock media")
            break
        video(d / "media/proof_of_concept/mock_proof.mp4")
        (d / "media/proof_of_concept/PROOF_REPORT.md").write_text("# MOCK proof report (local test pattern)\n")
    if gate == "GATE-07":
        run("cost", "add", "--kind", "ESTIMATE", "--stage", "production_planning", "--item", "mock", "--units", "1",
            "--unit-cost", "0", *p)
        (d / "PRODUCTION_PLAN.md").write_text("# MOCK PLAN\n")
    if gate == "GATE-08":
        video(d / "media/rough_cut/mock_rough.mp4")
        (d / "reviews/QC_ROUGH_CUT.md").write_text("# MOCK QC\n")
    if gate == "GATE-09":
        video(d / "media/final/mock_final.mp4")
        (d / "DELIVERY_CHECKLIST.md").write_text("# MOCK CHECKLIST\n- [x] mock\n")
        (d / "reviews/QC_FINAL.md").write_text("# MOCK QC FINAL\n")
    accept(gate)
    run("submit", gate, *p)
    run("approve", gate, "--by", "Smoke Tester", "--note", "mock", *p)
    run("validate", *p)

# --- quality-first features (mock) ---
run("submit", "GATE-09", *p, expect=2)  # already approved
run("cost", "add", "--kind", "ACTUAL", "--stage", "x", "--item", "i", "--units", "1", "--unit-cost", "1", *p, expect=2)  # no authorization
run("cost", "set-budget", "--amount", "50", "--by", "Smoke Tester", *p)
run("cost", "authorize", "--amount", "20", "--scope", "smoke batch", "--by", "Smoke Tester", *p)
run("attempt", "record", "--shot", "SH-001-01", "--capability", "video-generation", "--provider", "mock", "--outcome", "FAILED", *p, expect=2)
run("attempt", "record", "--shot", "SH-001-01", "--capability", "video-generation", "--provider", "mock", "--outcome", "FAILED",
    "--failure-category", "morphing", "--analysis", "mock: face morphs after 1s", "--cost", "2", "--authorization", "AU-0001", *p)
run("shot", "set-status", "--id", "SH-001-01", "--status", "APPROVED", *p, expect=2)  # not on the producer's word
if have_ffmpeg:
    run("inspect", "media/final/mock_final.mp4", "--frames", "3", *p)
    accept(None, target="shot:SH-001-01", cset_list=["shot_media"], files="media/final/mock_final.mp4")
    run("shot", "set-status", "--id", "SH-001-01", "--status", "APPROVED", *p)
run("report", "economics", *p)
run("teams", "check", *p)
run("models", "check", *p)
run("validate", *p)
run("pending", *p)
out = run("export", *p)
run("status", *p)
shutil.rmtree(tmp)
print("\nSMOKE RESULT:", "FAILED %d step(s)" % len(fails) if fails else "ALL STEPS PASSED (mock content, no external calls)")
sys.exit(1 if fails else 0)

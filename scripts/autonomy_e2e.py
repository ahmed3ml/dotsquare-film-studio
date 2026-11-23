#!/usr/bin/env python3
"""Acceptance test for the autonomous orchestrator (mock agents + mock media; no network, no model calls, no spend).

Scenarios (each in its own temp projects dir; never touches projects/MUTAH-PILOT):
 A. One brief -> full pipeline -> real rendered + inspected MP4, with automatic review/repair loops, policy approvals,
    and the only human stop at GATE-07 (budget); external consultant requirement and CA-001 hold enforced and released
    by a simulated human with evidence.
 B. Crash recovery: background runner (real OS process) is killed with SIGKILL mid-job, restarted, completes; no
    duplicate review cycles; a second concurrent runner is refused by the run lock.
 C. Loop limit: a shot that always fails review is diagnosed and BLOCKED after 3 failed cycles while independent work
    (other shots, sound) continues.
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "studio"))
import studio  # noqa: E402

studio.build_parser()  # registers ext/autonomy/orchestrator
import autonomy as A  # noqa: E402
import orchestrator as O  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print("%s %s%s" % ("PASS" if cond else "FAIL", name, "" if cond else "  -> %s" % detail))


def cli(base, *argv):
    p = subprocess.run([sys.executable, str(ROOT / "studio" / "studio.py"), "--projects-dir", str(base)] + list(argv),
                       capture_output=True, text=True, cwd=str(ROOT))
    return p.returncode, p.stdout + p.stderr


def new_film(base, pid, plan, client_gates=None):
    args = ["film", "new", "--id", pid, "--title", "Test %s" % pid, "--runtime", "1", "--backend", "mock",
            "--brief", "Create a short photorealistic film (TEST)", "--mock-plan", json.dumps(plan)]
    code, out = cli(base, *args)
    assert code == 0, out
    return base / pid


def state(pdir):
    return json.loads((pdir / "orchestrator" / "state.json").read_text())


def run_fg(base, pid):
    return cli(base, "film", "run", "-p", pid)


# ---------------------------------------------------------------- A
def scenario_a():
    base = Path(tempfile.mkdtemp(prefix="auto_a_"))
    pdir = new_film(base, "AUTO-A", {"shots": 3, "shot_seconds": 1.2, "hold_shot": 3,
                                     "fail_reviews": {"gate:GATE-02": 1, "shot:SH-001-02": 1}})
    pol = A.load_policy(pdir)
    pol["external_requirements"] = [{"id": "consultant_review", "gate": "GATE-03", "status": "PENDING_EXTERNAL_REVIEW",
                                     "description": "independent human consultant (test)"}]
    pol["content_holds"] = [{"id": "CA-001", "tag": "[CA-001]", "status": "PENDING", "description": "explicit injury (test)"}]
    A.save_policy(pdir, pol)
    run_fg(base, "AUTO-A")
    st = studio.load(pdir)
    check("A1 automatic handoffs reach GATE-03 submission", st["gates"]["GATE-03"]["state"] == "PENDING_APPROVAL",
          st["gates"]["GATE-03"]["state"])
    check("A2 policy cannot approve GATE-03 while consultant review is pending",
          any(w.get("gate") == "GATE-03" and w.get("external") for w in state(pdir)["waiting"]), state(pdir)["waiting"])
    code, out = cli(base, "approve", "GATE-03", "--by", "Some Human", "-p", "AUTO-A")
    check("A3 even a human approval of GATE-03 is refused without the consultant evidence", code != 0 and "EXTERNAL_REQUIRED" in out, out)
    (pdir / "reviews" / "external").mkdir(parents=True, exist_ok=True)
    (pdir / "reviews" / "external" / "consultant_report_TEST.md").write_text("TEST consultant findings (simulated human)\n")
    code, out = cli(base, "external", "record", "--req", "consultant_review", "--by", "Test Human",
                    "--evidence", "reviews/external/consultant_report_TEST.md", "-p", "AUTO-A")
    check("A4 human records consultant evidence", code == 0, out)
    run_fg(base, "AUTO-A")
    st = studio.load(pdir)
    check("A5 GATE-03..06 then approved by policy; stops at GATE-07 (hard human gate)",
          st["gates"]["GATE-06"]["state"] == "APPROVED" and st["gates"]["GATE-07"]["state"] == "PENDING_APPROVAL",
          {g: st["gates"][g]["state"] for g in st["gates"]})
    code, out = cli(base, "approve", "GATE-07", "--policy", "-p", "AUTO-A")
    check("A6 GATE-07 can never be policy-approved", code != 0 and "HUMAN_REQUIRED" in out, out)
    cli(base, "approve", "GATE-07", "--by", "Test Human", "--note", "test budget", "-p", "AUTO-A")
    cli(base, "spec", "set", "--width", "1280", "--height", "720", "--fps", "24", "--duration-s", "3.6", "--by", "Test Human", "-p", "AUTO-A")
    run_fg(base, "AUTO-A")
    s = state(pdir)
    rows = studio.read_csv(pdir / "GENERATION_LOG.csv")
    held = [r for r in rows if r["shot_id"] == "SH-001-03"]
    check("A7 CA-001 tagged shot never generated before release; other work continued",
          not held and studio.load(pdir)["stages"]["video_production"]["state"] == "IN_PROGRESS" and
          any(w["type"] == "hold" for w in s["waiting"]), (len(held), s["waiting"]))
    code, out = cli(base, "generate", "--shot", "SH-001-03", "--capability", "image-generation", "--phase", "production", "-p", "AUTO-A")
    check("A8 engine refuses generation of the held shot", code != 0 and "HOLD_ACTIVE" in out, out)
    (pdir / "reviews" / "external" / "client_CA-001_TEST.md").write_text("TEST client approval (simulated)\n")
    cli(base, "hold", "release", "--id", "CA-001", "--by", "Test Client", "--evidence", "reviews/external/client_CA-001_TEST.md", "-p", "AUTO-A")
    run_fg(base, "AUTO-A")
    s = state(pdir)
    st = studio.load(pdir)
    final = pdir / O.FINAL
    check("A9 film delivered after the release (GATE-08/09 by policy)", s["status"] == "DELIVERED" and final.exists(), s["status"])
    if final.exists():
        info = studio.probe(final)
        v = [x for x in info["streams"] if x["type"] == "video"]
        a = [x for x in info["streams"] if x["type"] == "audio"]
        check("A10 final MP4 really rendered: video+audio, expected duration", v and a and abs(info["duration_s"] - 3.6) < 0.3, info)
        code, out = cli(base, "techcheck", O.FINAL, "-p", "AUTO-A")
        check("A11 final MP4 passes techcheck against the delivery spec", code == 0, out)
        check("A12 delivery flagged SYNTHETIC (mock success != real production readiness)", s["delivered"]["synthetic"] is True, s["delivered"])
    gates = {g: (st["gates"][g]["state"], st["gates"][g]["approved_by"]) for g in st["gates"]}
    check("A13 approvals recorded: GATE-07 by human, others by POLICY",
          gates["GATE-07"][1] == "Test Human" and all(gates[g][1] == "POLICY:AUTO_APPROVAL" for g in gates if g != "GATE-07"), gates)
    revs = st["reviews"]
    check("A14 repair loops ran (rejected then accepted) for GATE-02 and SH-001-02",
          [r["status"] for r in revs if r["target"] == "gate:GATE-02"] == ["REVISION_NEEDED", "ACCEPTED"] and
          [r["status"] for r in revs if r["target"] == "shot:SH-001-02"] == ["REVISION_NEEDED", "ACCEPTED"])
    check("A15 previous versions preserved before revisions", any(p.name.startswith("gate_GATE-02_before_revise") for p in (pdir / "versions").iterdir()))
    code, out = cli(base, "validate", "-p", "AUTO-A")
    check("A16 engine validate clean after autonomous run", code == 0, out[-400:])
    shutil.rmtree(base)


# ---------------------------------------------------------------- B
def scenario_b():
    base = Path(tempfile.mkdtemp(prefix="auto_b_"))
    pdir = new_film(base, "AUTO-B", {"shots": 2, "shot_seconds": 1, "slow_s": 1.0})
    code, out = cli(base, "film", "start", "-p", "AUTO-B")
    check("B1 background runner starts", code == 0 and "started" in out, out)
    time.sleep(4.5)
    code, out = cli(base, "film", "run", "-p", "AUTO-B")
    check("B2 second concurrent runner refused by run lock", code != 0 and "ALREADY_RUNNING" in out, out)
    pid = int((pdir / "orchestrator" / "run.pid").read_text())
    os.killpg(os.getpgid(pid), signal.SIGKILL)  # hard crash, no cleanup
    time.sleep(0.5)
    s = state(pdir)
    running = [k for k, j in s["jobs"].items() if j["status"] == "RUNNING"]
    check("B3 crash left a RUNNING job behind (interrupted mid-job)", len(running) == 1, s["jobs"])
    run_fg(base, "AUTO-B")
    s = state(pdir)
    j = s["jobs"].get(running[0], {}) if running else {}
    check("B4 restart recovered and re-ran the interrupted job idempotently", j.get("status") == "DONE" and j.get("attempts") == 2, j)
    st = studio.load(pdir)
    opened = {}
    for r in st["reviews"]:
        opened.setdefault(r["target"], []).append(r["status"])
    dup = {t: v for t, v in opened.items() if v.count("OPEN") > 0 or len(v) > 1}
    check("B5 no duplicate or orphan review cycles after the crash", not dup, dup)
    check("B6 resumed to the GATE-07 human stop", st["gates"]["GATE-07"]["state"] == "PENDING_APPROVAL", st["gates"]["GATE-07"]["state"])
    code, out = cli(base, "film", "pause", "-p", "AUTO-B")
    code, out = run_fg(base, "AUTO-B")
    check("B7 pause flag stops the runner before any job", state(pdir)["status"] == "PAUSED", state(pdir)["status"])
    shutil.rmtree(base)


# ---------------------------------------------------------------- C
def scenario_c():
    base = Path(tempfile.mkdtemp(prefix="auto_c_"))
    pdir = new_film(base, "AUTO-C", {"shots": 3, "shot_seconds": 1, "always_fail": ["shot:SH-001-02"]})
    run_fg(base, "AUTO-C")
    cli(base, "approve", "GATE-07", "--by", "Test Human", "-p", "AUTO-C")
    run_fg(base, "AUTO-C")
    s = state(pdir)
    st = studio.load(pdir)
    fails = [r for r in st["reviews"] if r["target"] == "shot:SH-001-02"]
    check("C1 exactly 3 failed review cycles, then no more attempts", len(fails) == 3 and all(r["status"] == "REVISION_NEEDED" for r in fails),
          [r["status"] for r in fails])
    check("C2 shot BLOCKED with a diagnosis report", "shot:SH-001-02" in s["blocked"] and
          any(p.name.startswith("shot_SH-001-02") for p in (pdir / "reviews" / "diagnosis").iterdir()), s["blocked"])
    others = [studio.load(pdir)["stages"][n]["state"] for n in ("sound", "image_production")]
    ok_shots = [r["shot_id"] for r in studio.read_csv(pdir / "SHOT_LIST.csv") if r["status"] == "APPROVED"]
    check("C3 independent work continued (other shots approved, sound/keyframes done)",
          "SH-001-01" in ok_shots and "SH-001-03" in ok_shots and others == ["DONE", "DONE"], (ok_shots, others))
    check("C4 run ends BLOCKED/WAITING (not looping) with an explicit reason", s["status"] in ("BLOCKED", "WAITING_HUMAN"), s["status"])
    shutil.rmtree(base)


if __name__ == "__main__":
    t0 = time.time()
    for fn in (scenario_a, scenario_b, scenario_c):
        print("== %s" % fn.__name__)
        fn()
    bad = [n for n, ok in results if not ok]
    print("\nAUTONOMY E2E: %d/%d passed in %.0fs%s" % (len(results) - len(bad), len(results), time.time() - t0,
                                                      "" if not bad else " | FAILED: " + ", ".join(bad)))
    sys.exit(1 if bad else 0)

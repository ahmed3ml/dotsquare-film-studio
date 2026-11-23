"""Unit tests for the autonomy layer: approval policy enforcement, external requirements, content holds, hook guard
for orchestrator/policy files, provider selection without evidence, orchestrator recovery and lock.
The full pipeline acceptance test (real background process, SIGKILL recovery, final MP4) is scripts/autonomy_e2e.py."""
import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "studio"))
import studio  # noqa: E402

studio.build_parser()
import autonomy as A  # noqa: E402
import backends as B  # noqa: E402
import orchestrator as O  # noqa: E402
import providers as P  # noqa: E402


def run(base, *argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        code = studio.main(["--projects-dir", str(base)] + list(argv))
    return code, buf.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="t_auto_"))
        code, out = run(self.base, "film", "new", "--id", "T-AUTO", "--title", "T", "--backend", "mock", "--brief", "test",
                        "--mock-plan", json.dumps({"shots": 2, "shot_seconds": 1}))
        self.assertEqual(code, 0, out)
        self.pdir = self.base / "T-AUTO"

    def tearDown(self):
        shutil.rmtree(self.base)

    def p(self, *argv):
        return run(self.base, *(list(argv) + ["-p", "T-AUTO"]))


class TestPolicy(Base):
    def test_policy_approval_needs_submitted_reviewed_gate_and_scope(self):
        o = O.Orchestrator(self.pdir)
        o.run(max_jobs=4)  # intake, create, review GATE-01 (then submit + policy approve), create story
        st = studio.load(self.pdir)
        self.assertEqual(st["gates"]["GATE-01"]["approved_by"], A.POLICY_APPROVER)
        lines = (self.pdir / "APPROVAL_LOG.md").read_text()
        self.assertIn("policy sha", lines)

    def test_disabled_policy_and_changed_scope_refuse(self):
        O.Orchestrator(self.pdir).run(max_jobs=1)  # intake -> scope locked
        pol = A.load_policy(self.pdir)
        self.assertTrue(pol["scope_lock"]["sha256"])
        with open(self.pdir / "PROJECT.md", "a") as f:
            f.write("\nscope change\n")
        probs = A.approval_problems(self.pdir, studio.load(self.pdir), "GATE-01", True)
        self.assertTrue(any(p.startswith("SCOPE_CHANGED") for p in probs), probs)
        self.p("policy", "disable", "--by", "Human")
        probs = A.approval_problems(self.pdir, studio.load(self.pdir), "GATE-01", True)
        self.assertTrue(any(p.startswith("POLICY_DISABLED") for p in probs))

    def test_hard_gate_cannot_be_set_to_policy(self):
        code, out = self.p("policy", "set", "--gate", "GATE-07", "--mode", "policy", "--by", "Human")
        self.assertNotEqual(code, 0)
        self.assertIn("hard human gate", out)

    def test_client_gate_needs_human(self):
        pol = A.load_policy(self.pdir)
        pol["client_approval_gates"] = ["GATE-02"]
        A.save_policy(self.pdir, pol)
        self.assertEqual(A.gate_mode(A.load_policy(self.pdir), "GATE-02"), "human")

    def test_policy_commands_need_a_human_name(self):
        code, out = self.p("policy", "enable", "--by", "POLICY:bot")
        self.assertNotEqual(code, 0)

    def test_external_record_needs_existing_evidence(self):
        pol = A.load_policy(self.pdir)
        pol["external_requirements"] = [{"id": "consultant_review", "gate": "GATE-03", "status": "PENDING"}]
        A.save_policy(self.pdir, pol)
        code, out = self.p("external", "record", "--req", "consultant_review", "--by", "H", "--evidence", "nope.md")
        self.assertNotEqual(code, 0)
        (self.pdir / "ev.md").write_text("x")
        code, out = self.p("external", "record", "--req", "consultant_review", "--by", "H", "--evidence", "ev.md")
        self.assertEqual(code, 0, out)
        self.assertEqual(A.external_pending(A.load_policy(self.pdir), "GATE-03"), [])


class TestHoldsAndHook(Base):
    def test_hold_tag_detection(self):
        pol = A.load_policy(self.pdir)
        pol["content_holds"] = [{"id": "CA-001", "tag": "[CA-001]", "status": "PENDING"}]
        A.save_policy(self.pdir, pol)
        cols = studio.CSV_HEADERS["SHOT_LIST.csv"].split(",")
        row = {c: "" for c in cols}
        row.update(shot_id="SH-001-01", seq="1", duration_s="1", notes="[CA-001] injury", status="PLANNED", retries="0")
        (self.pdir / "SHOT_LIST.csv").write_text(",".join(cols) + "\n" + ",".join(row[c] for c in cols) + "\n")
        self.assertEqual(len(A.shot_holds(self.pdir, "SH-001-01")), 1)
        with self.assertRaises(studio.StudioError):
            A.hold_check(self.pdir, "SH-001-01")

    def test_hook_blocks_policy_providers_and_orchestrator_files(self):
        for rel in ("approval_policy.json", "providers.json", "orchestrator/state.json"):
            payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(self.pdir / rel)}})
            r = subprocess.run([sys.executable, str(ROOT / "studio" / "studio.py"), "--projects-dir", str(self.base), "hook-guard"],
                               input=payload, capture_output=True, text=True)
            self.assertEqual(r.returncode, 2, rel)


class TestOrchestrator(Base):
    def test_lock_prevents_second_runner(self):
        o1 = O.Orchestrator(self.pdir)
        o1.acquire()
        try:
            with self.assertRaises(studio.StudioError):
                O.Orchestrator(self.pdir).acquire()
        finally:
            o1.release()

    def test_interrupted_job_is_recovered_and_rerun(self):
        class Crash(B.MockBackend):
            def run(self, job, *a):
                if job["kind"] == "create":
                    raise KeyboardInterrupt("simulated crash")
                return super().run(job, *a)
        o = O.Orchestrator(self.pdir, backend=Crash())
        with self.assertRaises(KeyboardInterrupt):
            o.run()
        st = json.loads((self.pdir / "orchestrator/state.json").read_text())
        self.assertEqual(st["jobs"]["create:research"]["status"], "RUNNING")
        o2 = O.Orchestrator(self.pdir)
        o2.run(max_jobs=1)
        self.assertEqual(o2.state["jobs"]["create:research"]["status"], "DONE")
        self.assertEqual(o2.state["jobs"]["create:research"]["attempts"], 2)

    def test_plan_is_pure(self):
        o = O.Orchestrator(self.pdir)
        before = (self.pdir / "PROJECT_STATUS.json").read_text()
        o.plan()
        o.plan()
        self.assertEqual(before, (self.pdir / "PROJECT_STATUS.json").read_text())

    def test_status_text_is_arabic_and_hides_ids(self):
        txt = O.status_text(O.Orchestrator(self.pdir))
        self.assertIn("المرحلة", txt)
        self.assertNotIn("TK-0", txt)


class TestProviders(unittest.TestCase):
    def test_rank_ignores_unevidenced_claims(self):
        cfg = P.load_config(Path(tempfile.mkdtemp()))
        cfg["providers"]["vendor"] = {"type": "connector", "capabilities": ["image-to-video"],
                                      "profile": {"cinematic_realism": {"value": 5}}}
        r = [x for x in P.rank(cfg, "image-to-video", Path("/nonexistent")) if x["provider"] == "vendor"][0]
        self.assertEqual(r["score"], 0)
        self.assertIn("cinematic_realism", r["unknown_metrics"])
        self.assertFalse(r["eligible"])  # price unknown + commercial terms unverified

    def test_video_caps_share_prompt(self):
        self.assertEqual(P.default_prompt("image-to-video", "SH-001-01"), P.default_prompt("video-generation", "SH-001-01"))
        self.assertEqual(P.default_prompt("music-generation", "MUSIC"), "prompts/audio/MUSIC_music.md")

    def test_claude_command_has_no_bypass_and_denies_engine_mutations(self):
        b = B.ClaudeBackend(bin_path="/bin/echo")
        cmd = b.command({"agent": "screenwriter", "key": "k", "extra": {}}, "X", "brief")
        self.assertNotIn("--dangerously-skip-permissions", cmd)
        self.assertIn("acceptEdits", cmd)
        self.assertIn("Bash(python3 studio/studio.py approve:*)", cmd)
        self.assertNotIn("Bash", [c for c in cmd[cmd.index("--allowedTools") + 1:cmd.index("--disallowedTools")]])


if __name__ == "__main__":
    unittest.main()

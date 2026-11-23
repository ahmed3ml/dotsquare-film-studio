"""Tests for the quality-first upgrade: independent reviews, evidence rules, adaptive retry, authorizations,
provider routing, inspection, hook guard, team registry, model policy, upgrade."""
import contextlib
import io
import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_studio import ROOT, WorkflowBase, studio, studio_ext_criteria, run  # noqa: E402
import ext  # noqa: E402
import providers  # noqa: E402

HAVE_FFMPEG = shutil.which("ffmpeg") is not None and studio.has_ffprobe()
SHOT_H = studio.CSV_HEADERS["SHOT_LIST.csv"]


def verdict(cset, result="PASS", evidence_files=("reviews/evidence/mock_evidence.txt",), overrides=None):
    crit = {}
    for c in studio_ext_criteria()["sets"][cset]:
        crit[c["id"]] = {"result": result, "evidence": "MOCK evidence: concrete observation for %s" % c["id"],
                         "evidence_files": list(evidence_files)}
    for k, v in (overrides or {}).items():
        crit[k] = v
    return {"criteria": crit, "summary": "mock"}


class QualityBase(WorkflowBase):
    def open_review(self, target, cset, creator="maker", files=None):
        args = ["review", "open", "--target", target, "--set", cset, "--creator", creator]
        if files:
            args += ["--files", files]
        code, out = self.r(*args)
        self.assertEqual(code, 0, out)
        return re.search(r"(RC-\d{4}) opened", out).group(1)

    def close_review(self, rid, v, reviewer="checker", *extra):
        self.fill("reviews/evidence/mock_evidence.txt", "mock evidence\n")
        vf = self.d / ("reviews/verdicts/%s.json" % rid)
        vf.parent.mkdir(parents=True, exist_ok=True)
        vf.write_text(json.dumps(v))
        return self.r("review", "close", rid, "--reviewer", reviewer, "--verdict-file", str(vf), *extra)

    def set_shots(self, *rows):
        self.fill("SHOT_LIST.csv", SHOT_H + "\n" + "\n".join(rows) + "\n")

    def shot(self, sid="SH-001-01", status="PLANNED"):
        cols = SHOT_H.split(",")
        vals = {"shot_id": sid, "scene_id": "SC-001", "seq": "1", "description": "mock", "duration_s": "4",
                "status": status, "retries": "0"}
        return ",".join(vals.get(c, "") for c in cols)

    def approve_gates_to(self, last):
        docs = {"GATE-01": ["HISTORICAL_BIBLE.md"], "GATE-02": ["STORY_TREATMENT.md"],
                "GATE-03": ["SCREENPLAY.md", "reviews/SCRIPT_REVIEW.md"],
                "GATE-04": ["CHARACTER_BIBLE.md", "ENVIRONMENT_BIBLE.md", "VISUAL_STYLE_GUIDE.md"]}
        for g in studio.GATE_IDS[:studio.GATE_IDS.index(last) + 1]:
            for f in docs.get(g, []):
                self.fill(f)
            if g == "GATE-05":
                self.set_shots(self.shot())
            self.assertEqual(self.sub(g)[0], 0)
            self.assertEqual(self.r("approve", g, "--by", "Human")[0], 0)


class ReviewLoop(QualityBase):
    def test_submit_blocked_without_review(self):
        self.fill("HISTORICAL_BIBLE.md")
        code, out = self.r("submit", "GATE-01")
        self.assertEqual(code, 2)
        self.assertIn("REVIEW_REQUIRED", out)

    def test_creator_cannot_review_own_work(self):
        self.fill("HISTORICAL_BIBLE.md")
        rid = self.open_review("gate:GATE-01", "historical_research", creator="alice")
        code, out = self.close_review(rid, verdict("historical_research"), reviewer="Alice")
        self.assertEqual(code, 2)
        self.assertIn("NOT_INDEPENDENT", out)
        code, out = self.close_review(rid, verdict("historical_research"), "Alice", "--same-actor-reason", "no other agent")
        self.assertIn("NEEDS_HUMAN_REVIEW", out)  # never auto-accepted

    def test_fail_then_revision_round_then_accept(self):
        self.fill("HISTORICAL_BIBLE.md")
        rid = self.open_review("gate:GATE-01", "historical_research")
        bad = verdict("historical_research", overrides={"HR-2": {"result": "FAIL", "evidence": "SRC-002 does not exist in any catalogue checked"}})
        code, out = self.close_review(rid, bad)
        self.assertIn("REVISION_NEEDED", out)
        self.assertIn("HR-2", out)
        self.assertEqual(self.r("submit", "GATE-01")[0], 2)
        self.fill("HISTORICAL_BIBLE.md", "# revised\n")
        rid2 = self.open_review("gate:GATE-01", "historical_research")
        self.assertNotEqual(rid, rid2)
        code, out = self.close_review(rid2, verdict("historical_research"))
        self.assertIn("ACCEPTED", out)
        self.assertEqual(self.r("submit", "GATE-01")[0], 0)
        cyc = json.loads((self.d / "PROJECT_STATUS.json").read_text())["reviews"][-1]
        self.assertEqual(cyc["round"], 2)

    def test_evidence_is_mandatory_and_media_needs_files(self):
        self.fill("HISTORICAL_BIBLE.md")
        rid = self.open_review("gate:GATE-01", "historical_research")
        v = verdict("historical_research")
        v["criteria"]["HR-1"]["evidence"] = "ok"
        code, out = self.close_review(rid, v)
        self.assertEqual(code, 2)
        self.assertIn("concrete evidence", out)
        self.fill("SHOT_LIST.csv", SHOT_H + "\n" + self.shot() + "\n")
        self.fill("media/video/a.mp4", "x")
        rid = self.open_review("shot:SH-001-01", "shot_media", files="media/video/a.mp4")
        v = verdict("shot_media", evidence_files=())
        code, out = self.close_review(rid, v)
        self.assertEqual(code, 2)
        self.assertIn("cannot PASS without evidence_files", out)

    def test_not_verified_requires_human_ack_with_note(self):
        self.fill("media/video/a.mp4", "x")
        rid = self.open_review("shot:SH-001-01", "shot_media", files="media/video/a.mp4")
        v = verdict("shot_media", evidence_files=())
        for k in v["criteria"]:
            v["criteria"][k] = {"result": "NOT_VERIFIED", "evidence": "no image viewing capability available in this session"}
        code, out = self.close_review(rid, v)
        self.assertIn("NEEDS_HUMAN_REVIEW", out)
        self.assertEqual(self.r("review", "human-ack", rid, "--by", "Human")[0], 2)
        self.assertEqual(self.r("review", "human-ack", rid, "--by", "Human", "--note", "watched the clip twice")[0], 0)

    def test_review_stale_after_change_and_void_during_review(self):
        self.fill("HISTORICAL_BIBLE.md")
        rid = self.open_review("gate:GATE-01", "historical_research")
        self.fill("HISTORICAL_BIBLE.md", "# changed while under review\n")
        code, out = self.close_review(rid, verdict("historical_research"))
        self.assertEqual(code, 2)
        self.assertIn("VOID", out)
        self.fill("HISTORICAL_BIBLE.md", "# stable\n")
        rid = self.open_review("gate:GATE-01", "historical_research")
        self.close_review(rid, verdict("historical_research"))
        self.fill("HISTORICAL_BIBLE.md", "# edited after acceptance\n")
        code, out = self.r("submit", "GATE-01")
        self.assertEqual(code, 2)
        self.assertIn("REVIEW_STALE", out)

    def test_unknown_or_missing_criteria_rejected(self):
        self.fill("HISTORICAL_BIBLE.md")
        rid = self.open_review("gate:GATE-01", "historical_research")
        v = verdict("historical_research")
        del v["criteria"]["HR-3"]
        code, out = self.close_review(rid, v)
        self.assertEqual(code, 2)
        self.assertIn("missing criterion HR-3", out)

    def test_shot_cannot_be_approved_on_producers_word(self):
        self.set_shots(self.shot())
        self.fill("media/video/a.mp4", "x")
        code, out = self.r("shot", "set-status", "--id", "SH-001-01", "--status", "APPROVED")
        self.assertEqual(code, 2)
        self.assertIn("REVIEW_REQUIRED", out)
        rid = self.open_review("shot:SH-001-01", "shot_media", files="media/video/a.mp4")
        self.close_review(rid, verdict("shot_media"))
        self.assertEqual(self.r("shot", "set-status", "--id", "SH-001-01", "--status", "APPROVED")[0], 0)
        self.assertEqual(self.r("validate")[0], 0, self.r("validate")[1])
        self.fill("media/video/a.mp4", "replaced output")
        self.assertIn("SHOT_APPROVED_WITHOUT_REVIEW", self.r("validate")[1])

    def test_criteria_standards_complete(self):
        crit = studio_ext_criteria()
        self.assertEqual(set(crit["gate_reviews"]), set(studio.GATE_IDS))
        for g, sets in crit["gate_reviews"].items():
            for s in sets:
                self.assertIn(s, crit["sets"])
        needed = ["historical", "dramatic", "consistency", "realism", "composition", "motion", "anatomical", "continuity",
                  "dialogue quality", "synchronization", "delivery specification"]
        blob = json.dumps(crit).lower()
        for k in needed:
            self.assertIn(k, blob, k)
        self.assertNotRegex(blob, r"score\s*(of|>=)\s*\d")  # no arbitrary numeric scoring


class Economics(QualityBase):
    def authorize(self, amount="100"):
        self.assertEqual(self.r("cost", "authorize", "--amount", amount, "--scope", "test batch", "--by", "Human")[0], 0)

    def rec(self, *extra, outcome="SUCCESS"):
        return self.r("attempt", "record", "--shot", "SH-001-01", "--capability", "video-generation", "--provider", "p",
                      "--model", "m1", "--outcome", outcome, *extra)

    def test_gate_approval_does_not_authorize_spend(self):
        self.approve_gates_to("GATE-04")
        st = json.loads((self.d / "PROJECT_STATUS.json").read_text())
        self.assertEqual(st["authorizations"], [])
        self.assertEqual(self.r("cost", "add", "--kind", "ACTUAL", "--stage", "x", "--item", "i", "--units", "1",
                                "--unit-cost", "1")[0], 2)

    def test_failed_attempt_needs_analysis_and_no_hard_cap(self):
        self.set_shots(self.shot())
        self.authorize()
        code, out = self.rec(outcome="FAILED")
        self.assertEqual(code, 2)
        self.assertIn("failure-category", out)
        fail = ["--failure-category", "anatomy", "--analysis", "extra finger at 00:02 on left hand", "--cost", "1.5",
                "--authorization", "AU-0001"]
        self.assertEqual(self.rec(*fail, outcome="FAILED")[0], 0)
        self.assertEqual(self.rec(*fail, outcome="FAILED")[0], 0)
        code, out = self.rec(*fail, outcome="FAILED")
        self.assertEqual(code, 2)
        self.assertIn("STRATEGY_CHANGE_REQUIRED", out)
        for i in range(6):  # well beyond the old cap of 3: allowed when strategy changes are reasoned
            self.assertEqual(self.rec(*fail, "--strategy-change", "attempt %d: simplified hand pose" % i, outcome="FAILED")[0], 0)
        rows = studio.read_csv(self.d / "GENERATION_LOG.csv")
        self.assertEqual(len(rows), 8)
        shots = studio.read_csv(self.d / "SHOT_LIST.csv")
        self.assertEqual(shots[0]["retries"], "8")
        self.assertEqual(self.r("validate")[0], 0, self.r("validate")[1])

    def test_spend_requires_authorization_and_is_capped_by_it(self):
        self.set_shots(self.shot())
        code, out = self.rec("--cost", "5")
        self.assertEqual(code, 2)
        self.assertIn("AUTHORIZATION_REQUIRED", out)
        self.authorize("10")
        self.assertEqual(self.rec("--cost", "6", "--authorization", "AU-0001")[0], 0)
        code, out = self.rec("--cost", "6", "--authorization", "AU-0001")
        self.assertEqual(code, 2)
        self.assertIn("AUTHORIZATION_EXCEEDED", out)

    def test_record_refuses_nonexistent_output(self):
        self.set_shots(self.shot())
        code, out = self.rec("--output", "media/video/ghost.mp4")
        self.assertEqual(code, 2)
        self.assertIn("does not exist", out)

    def test_economics_report(self):
        self.set_shots(self.shot())
        self.authorize("50")
        self.rec("--cost", "4", "--authorization", "AU-0001")
        self.rec("--cost", "2", "--authorization", "AU-0001", "--failure-category", "morphing", "--analysis", "face morphs after 2s", outcome="FAILED")
        self.rec("--cost", "3", "--authorization", "AU-0001", "--revision")
        code, out = self.r("report", "economics")
        self.assertEqual(code, 0)
        for token in ("m1", "محاولات=3", "فاشلة=1", "تكلفة الفاشلة: 2.00", "تكلفة المراجعات (Revisions): 3.00", "المتبقي 41.00"):
            self.assertIn(token, out)


class Providers(QualityBase):
    def test_default_route_is_prompt_file_and_logs_nothing(self):
        self.approve_gates_to("GATE-05")
        self.fill("prompts/video/SH-001-01.md", "prompt")
        code, out = self.r("generate", "--shot", "SH-001-01", "--capability", "video-generation", "--phase", "proof")
        self.assertEqual(code, 3)
        self.assertIn("BLOCKED_EXTERNAL_TOOL", out)
        self.assertEqual(studio.read_csv(self.d / "GENERATION_LOG.csv"), [])

    def test_gate_required_for_phase(self):
        self.fill("prompts/video/SH-001-01.md", "prompt")
        code, out = self.r("generate", "--shot", "SH-001-01", "--capability", "video-generation", "--phase", "production")
        self.assertEqual(code, 2)
        self.assertIn("GATE_REQUIRED", out)

    @unittest.skipUnless(HAVE_FFMPEG, "ffmpeg needed")
    def test_provider_replacement_by_route_mock_generates_synthetic_media(self):
        self.approve_gates_to("GATE-05")
        self.fill("prompts/video/SH-001-01.md", "prompt")
        (self.d / "providers.json").write_text(json.dumps({"schema_version": "1.0",
            "providers": providers.DEFAULT["providers"],
            "routes": {"video-generation": {"provider": "mock-ffmpeg"}, "image-generation": {"provider": "prompt-file"},
                       "speech-generation": {"provider": "prompt-file"}}}))
        code, out = self.r("generate", "--shot", "SH-001-01", "--capability", "video-generation", "--phase", "proof", "--units", "1")
        self.assertEqual(code, 0, out)
        rows = studio.read_csv(self.d / "GENERATION_LOG.csv")
        self.assertEqual(rows[0]["synthetic"], "yes")
        self.assertEqual(rows[0]["provider"], "mock-ffmpeg")
        self.assertTrue((self.d / rows[0]["output_path"]).exists())
        self.assertEqual(rows[0]["output_sha256"], studio.sha256_file(self.d / rows[0]["output_path"]))
        code, out = self.r("validate")
        self.assertIn("SYNTHETIC_OUTPUT", out)

    def test_connector_and_command_rules(self):
        self.approve_gates_to("GATE-05")
        self.fill("prompts/video/SH-001-01.md", "prompt")
        cfg = {"providers": {"conn": {"type": "connector", "capabilities": ["video-generation"],
                                      "pricing": {"video-generation": {"unit": "second", "price": None}}}},
               "routes": {"video-generation": {"provider": "conn"}}}
        (self.d / "providers.json").write_text(json.dumps(cfg))
        code, out = self.r("generate", "--shot", "SH-001-01", "--capability", "video-generation", "--phase", "proof")
        self.assertEqual(code, 2)
        self.assertIn("PRICE_UNKNOWN", out)
        args = ["generate", "--shot", "SH-001-01", "--capability", "video-generation", "--phase", "proof", "--estimated-cost", "3"]
        code, out = self.r(*args)
        self.assertIn("AUTHORIZATION_REQUIRED", out)
        self.r("cost", "authorize", "--amount", "10", "--scope", "t", "--by", "Human")
        code, out = self.r(*args, "--authorization", "AU-0001")
        self.assertIn("CONNECTOR_UNVERIFIED", out)
        code, out = self.r(*args, "--authorization", "AU-0001", "--connector-verified")
        self.assertEqual(code, 0)
        self.assertIn("PREFLIGHT_OK", out)
        self.assertEqual(studio.read_csv(self.d / "GENERATION_LOG.csv"), [])  # nothing executed, nothing logged

    def test_config_validation(self):
        with self.assertRaises(providers.ProviderError):
            providers.validate_config({"providers": {"x": {"type": "command", "command": "not-a-list"}}, "routes": {}})
        with self.assertRaises(providers.ProviderError):
            providers.validate_config({"providers": {"x": {"type": "connector", "capabilities": ["video-generation"], "auth_env": ["K=secret123"]}}, "routes": {}})
        with self.assertRaises(providers.ProviderError):
            providers.validate_config({"providers": {}, "routes": {"video-generation": {"provider": "nope"}}})

    def test_command_provider_executes_without_shell(self):
        if not HAVE_FFMPEG:
            self.skipTest("ffmpeg needed")
        prov = {"type": "command", "capabilities": ["image-generation"],
                "command": ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x64:rate=1", "-frames:v", "1", "{output}"]}
        out = self.tmp / "o.png"
        res = providers.execute(prov, "image-generation", {"prompt_file": "p", "output": out, "units": 1, "shot_id": "S"})
        self.assertEqual(res["status"], "SUCCESS")
        self.assertFalse(res["synthetic"])
        bad = dict(prov, command=["false"])
        self.assertEqual(providers.execute(bad, "image-generation", {"prompt_file": "p", "output": self.tmp / "n.png", "units": 1, "shot_id": "S"})["status"], "FAILED")

    def test_adapters_example_config_valid(self):
        cfg = json.loads((ROOT / "adapters/providers.example.json").read_text())
        providers.validate_config(cfg)


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg needed")
class Inspection(QualityBase):
    def make_video(self, rel, audio=True):
        p = self.d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=24:duration=2"]
        if audio:
            cmd += ["-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-shortest"]
        subprocess.run(cmd + ["-pix_fmt", "yuv420p", str(p)], check=True)
        return rel

    def test_inspect_extracts_frames_and_measures_audio(self):
        rel = self.make_video("media/video/t.mp4")
        code, out = self.r("inspect", rel, "--frames", "4")
        self.assertEqual(code, 0, out)
        ev = list((self.d / "reviews/evidence").glob("t_*/frame_*.jpg"))
        self.assertEqual(len(ev), 4)
        rep = json.loads(next((self.d / "reviews/evidence").glob("t_*/inspection.json")).read_text())
        self.assertIsNotNone(rep["audio"]["mean_volume_db"])
        self.assertIn("av_duration_offset_s", rep["audio"])

    def test_inspect_unavailable_for_non_media(self):
        self.fill("media/video/bad.mp4", "not media")
        code, out = self.r("inspect", "media/video/bad.mp4")
        self.assertNotEqual(code, 0)

    def test_inspect_reports_limitation_when_tools_missing(self):
        rel = self.make_video("media/video/t2.mp4")
        with mock.patch("shutil.which", return_value=None):
            code, out = self.r("inspect", rel)
        self.assertEqual(code, 2)
        self.assertIn("NOT_VERIFIED", out)

    def test_techcheck_against_delivery_spec(self):
        rel = self.make_video("media/video/t3.mp4")
        code, out = self.r("techcheck", rel)
        self.assertEqual(code, 2)
        self.assertIn("delivery_spec not set", out)
        stf = self.d / "PROJECT_STATUS.json"
        st = json.loads(stf.read_text())
        st["delivery_spec"] = {"width": 320, "height": 180, "fps": 24, "video_codec": "h264", "duration_s": 2}
        stf.write_text(json.dumps(st))
        code, out = self.r("techcheck", rel)
        self.assertEqual(code, 0, out)
        st["delivery_spec"]["width"] = 1920
        stf.write_text(json.dumps(st))
        code, out = self.r("techcheck", rel)
        self.assertEqual(code, 1)
        self.assertIn("FAIL width", out)


class HookGuard(QualityBase):
    def guard(self, path, tool="Write"):
        payload = json.dumps({"tool_name": tool, "tool_input": {"file_path": str(path)}, "cwd": str(self.tmp)})
        err = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(payload)), contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = studio.main(["--projects-dir", str(self.P), "hook-guard"])
        return code, err.getvalue()

    def test_blocks_engine_owned_files(self):
        for f in ("PROJECT_STATUS.json", "APPROVAL_LOG.md", "GENERATION_LOG.csv"):
            code, msg = self.guard(self.d / f)
            self.assertEqual(code, 2, f)
            self.assertIn("studio.py", msg)

    def test_blocks_locked_file_allows_drafts_and_outside(self):
        self.fill("HISTORICAL_BIBLE.md")
        self.sub("GATE-01")
        self.r("approve", "GATE-01", "--by", "Human")
        code, msg = self.guard(self.d / "HISTORICAL_BIBLE.md", "Edit")
        self.assertEqual(code, 2)
        self.assertIn("revise", msg)
        self.assertEqual(self.guard(self.d / "STORY_TREATMENT.md")[0], 0)
        self.assertEqual(self.guard(self.tmp / "elsewhere.txt")[0], 0)
        self.assertEqual(self.guard(self.d / "COST_TRACKER.csv")[0], 0)  # append-only lock: validate guards it
        self.r("revise", "GATE-01", "--by", "Human", "--note", "x")
        self.assertEqual(self.guard(self.d / "HISTORICAL_BIBLE.md")[0], 0)

    def test_settings_register_hook_and_script_is_executable_cli(self):
        s = json.loads((ROOT / ".claude/settings.json").read_text())
        hook = s["hooks"]["PreToolUse"][0]
        self.assertEqual(hook["matcher"], "Write|Edit")
        self.assertIn("hook-guard", hook["hooks"][0]["command"])
        # real subprocess run: stdin JSON in, exit code 2 out
        payload = json.dumps({"tool_input": {"file_path": str(self.d / "PROJECT_STATUS.json")}})
        r = subprocess.run([sys.executable, str(ROOT / "studio/studio.py"), "--projects-dir", str(self.P), "hook-guard"],
                           input=payload, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("BLOCKED", r.stderr)

    def test_agent_teams_not_enabled_by_project(self):
        s = json.loads((ROOT / ".claude/settings.json").read_text())
        self.assertNotIn("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS", s.get("env", {}))


class TeamsModelsUpgrade(QualityBase):
    def test_team_registry_valid_and_complete(self):
        reg, problems = ext.teams_problems()
        self.assertEqual(problems, [])
        self.assertEqual(set(reg["teams"]), {"historical_research", "story_development", "screenwriting_script_review",
                                             "film_direction_cinematography", "character_environment_design",
                                             "storyboard_shot_planning", "image_video_generation", "sound_voice_production",
                                             "editing_finishing", "independent_qa"})
        modes = {t["mode"] for t in reg["teams"].values()}
        self.assertEqual(modes, {"agent_team", "subagents", "hybrid"})  # hybrid architecture: right mechanism per task
        for t in reg["teams"].values():
            self.assertFalse(set(t["creators"]) & set(t["reviewers"]))

    def test_team_registry_detects_violations(self):
        reg = json.loads((ROOT / "standards/teams.json").read_text())
        reg["teams"]["historical_research"]["reviewers"] = ["historical-researcher"]
        reg["teams"]["story_development"]["fallback"] = "none"
        bad = reg
        orig = ext.C.STUDIO_ROOT
        tmp_root = self.tmp / "root"
        (tmp_root / "standards").mkdir(parents=True)
        shutil.copy(ROOT / "standards/criteria.json", tmp_root / "standards")
        (tmp_root / "standards/teams.json").write_text(json.dumps(bad))
        shutil.copytree(ROOT / ".claude", tmp_root / ".claude")
        try:
            ext.C.STUDIO_ROOT = tmp_root
            _, problems = ext.teams_problems()
        finally:
            ext.C.STUDIO_ROOT = orig
        joined = " ".join(problems)
        self.assertIn("creator also reviewer", joined)
        self.assertIn("needs a subagent fallback", joined)

    def test_teams_disabled_by_default_and_detected_when_enabled(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS", None)
            en, _ = ext.teams_enabled()
            if not en:  # user-level settings may enable it on a dev machine; project default is off
                self.assertIn("falls back to subagents", self.r("teams", "check")[1])
            os.environ["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"] = "1"
            self.assertTrue(ext.teams_enabled()[0])
            self.assertIn("ENABLED", self.r("teams", "check")[1])

    def test_model_policy_strongest_for_all_and_detects_drift(self):
        code, out = self.r("models", "check")
        self.assertEqual(code, 0, out)
        for f in (ROOT / ".claude/agents").glob("*.md"):
            self.assertEqual(ext.read_model(f), "opus", f.stem)  # no silent downgrade to cheaper models
        code, out = self.r("models", "check", "--model", "sonnet")
        self.assertEqual(code, 1)
        self.assertIn("MISMATCH", out)

    def test_upgrade_idempotent(self):
        self.assertIn("already up to date", self.r("upgrade")[1])

    def test_every_advertised_command_is_implemented(self):
        text = (ROOT / "README.md").read_text() + (ROOT / "CLAUDE.md").read_text()
        parser = studio.build_parser()
        subs = parser._subparsers._group_actions[0].choices
        for cmd in ("review", "shot", "attempt", "generate", "inspect", "techcheck", "report", "upgrade", "models", "teams", "hook-guard"):
            self.assertIn(cmd, subs)
        for c in (ROOT / ".claude/commands").glob("*.md"):
            body = c.read_text()
            for m in re.findall(r"studio\.py ([a-z\-]+)", body):
                self.assertIn(m, subs, "%s references unknown command %s" % (c.name, m))


if __name__ == "__main__":
    unittest.main()

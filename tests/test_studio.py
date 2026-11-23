"""Tests: skill discovery, agent config, templates, init, approval-state enforcement, cost, locks, adapters."""
import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "studio"))
import studio  # noqa: E402

SKILLS = ["film-project-manager", "historical-research", "story-development", "screenplay-writing", "script-review",
          "film-directing", "character-design", "production-design", "storyboard-planning", "ai-image-production",
          "ai-video-production", "sound-direction", "film-editing", "film-quality-control", "review-and-revision-loop",
          "media-inspection", "adaptive-retry", "agent-team-orchestration"]
AGENTS = ["executive-producer", "historical-researcher", "historical-fact-checker", "story-developer", "dramaturg-reviewer",
          "screenwriter", "script-reviewer", "film-director", "cinematographer", "visual-development-supervisor",
          "shot-planner", "image-producer", "video-producer", "sound-producer", "film-editor", "film-quality-reviewer",
          "continuity-supervisor", "audio-reviewer"]
REVIEWERS = ["historical-fact-checker", "dramaturg-reviewer", "script-reviewer", "film-quality-reviewer",
             "continuity-supervisor", "audio-reviewer"]


def frontmatter(path):
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, "no frontmatter in %s" % path
    data, key = {}, None
    for line in m.group(1).splitlines():
        if re.match(r"^\s+- ", line):
            data.setdefault(key, []).append(line.strip()[2:])
        else:
            k, _, v = line.partition(":")
            key = k.strip()
            data[key] = v.strip() or []
    return data


def run(*argv, projects=None):
    out, err = io.StringIO(), io.StringIO()
    args = (["--projects-dir", str(projects)] if projects else []) + list(argv)
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = studio.main(args)
    return code, out.getvalue() + err.getvalue()


def studio_ext_criteria():
    return json.loads((ROOT / "standards/criteria.json").read_text())


class StaticConfig(unittest.TestCase):
    def test_skill_discovery(self):
        dirs = sorted(p.name for p in (ROOT / ".claude/skills").iterdir() if p.is_dir())
        self.assertEqual(dirs, sorted(SKILLS))
        for s in SKILLS:
            fm = frontmatter(ROOT / ".claude/skills" / s / "SKILL.md")
            self.assertEqual(fm["name"], s)
            self.assertGreater(len(fm["description"]), 40, s)

    def test_agent_configuration(self):
        for a in AGENTS:
            fm = frontmatter(ROOT / ".claude/agents" / (a + ".md"))
            self.assertEqual(fm["name"], a)
            self.assertTrue(fm["description"])
            self.assertTrue(fm["tools"])
            if a != "executive-producer":
                self.assertNotIn("Agent", fm["tools"])
            self.assertTrue(fm["skills"], a)
            for s in fm["skills"]:
                self.assertIn(s, SKILLS, "%s references unknown skill %s" % (a, s))
        used = {s for a in AGENTS for s in frontmatter(ROOT / ".claude/agents" / (a + ".md"))["skills"]}
        self.assertEqual(used, set(SKILLS), "every skill must be loaded by some agent")

    def test_reviewers_cannot_edit_work_products(self):
        for a in REVIEWERS:
            self.assertNotIn("Edit", frontmatter(ROOT / ".claude/agents" / (a + ".md"))["tools"], a)

    def test_settings_and_commands(self):
        s = json.loads((ROOT / ".claude/settings.json").read_text())
        self.assertEqual(s["agent"], "executive-producer")
        self.assertIn("Bash(python3 studio/studio.py approve:*)", s["permissions"]["ask"])
        cmds = {p.stem for p in (ROOT / ".claude/commands").glob("*.md")}
        for c in ("studio-team", "studio-review", "studio-authorize", "studio-economics", "studio-new", "studio-status", "studio-approvals", "studio-approve", "studio-revise",
                  "studio-reject", "studio-plan", "studio-next", "studio-cost", "studio-export"):
            self.assertIn(c, cmds)

    def test_templates_exist_and_csv_headers(self):
        for f in studio.ROOT_FILES:
            if f != "PROJECT_STATUS.json":
                self.assertTrue((ROOT / "templates" / f).exists(), f)
        for fn, header in studio.CSV_HEADERS.items():
            self.assertEqual((ROOT / "templates" / fn).read_text().strip(), header)

    def test_gates_and_stages_consistent(self):
        self.assertEqual(len(studio.GATES), 9)
        for s in studio.STAGES:
            if s[1]:
                self.assertIn(s[1], studio.GATE_IDS)
            for r in s[2]:
                self.assertIn(r, studio.STAGE_NAMES)

    def test_adapter_contract(self):
        spec = json.loads((ROOT / "adapters/adapters.json").read_text())["adapters"]
        self.assertEqual(set(spec), {"image-generation", "video-generation", "speech-generation", "local-files",
                                     "video-editing", "audio-processing", "ffmpeg", "media-inspection"})
        for k, a in spec.items():
            self.assertIn("fallback", a, k)


class WorkflowBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.P = self.tmp / "projects"
        code, out = run("init", "--id", "TEST-01", "--title", "Test", "--concept-approved", projects=self.P)
        self.assertEqual(code, 0, out)
        self.d = self.P / "TEST-01"

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def r(self, *a):
        return run(*a, "-p", "TEST-01", projects=self.P)

    def accept(self, gate, reviewer="independent-reviewer", creator="creator-agent"):
        """Run a genuine review cycle (mock evidence) so a gate becomes submittable."""
        cs = studio_ext_criteria()["gate_reviews"][gate]
        for cset in cs:
            code, out = self.r("review", "open", "--target", "gate:" + gate, "--set", cset, "--creator", creator)
            self.assertEqual(code, 0, out)
            rid = re.search(r"(RC-\d{4}) opened", out).group(1)
            self.fill("reviews/evidence/mock_evidence.txt", "mock evidence file\n")
            crit = {c["id"]: {"result": "PASS", "evidence": "MOCK evidence: inspected the file and found it consistent",
                              "evidence_files": ["reviews/evidence/mock_evidence.txt"]}
                    for c in studio_ext_criteria()["sets"][cset]}
            vf = self.d / ("reviews/verdicts/%s.json" % rid)
            vf.parent.mkdir(parents=True, exist_ok=True)
            vf.write_text(json.dumps({"criteria": crit, "summary": "mock"}))
            code, out = self.r("review", "close", rid, "--reviewer", reviewer, "--verdict-file", str(vf))
            self.assertEqual(code, 0, out)

    def sub(self, gate):
        self.accept(gate)
        return self.r("submit", gate)

    def fill(self, rel, text="# MOCK content\nDone.\n"):
        (self.d / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.d / rel).write_text(text, encoding="utf-8")


class Init(WorkflowBase):
    def test_all_files_created_and_valid(self):
        for f in studio.ROOT_FILES + studio.EXTRA_FILES:
            self.assertTrue((self.d / f).exists(), f)
        self.assertEqual(self.r("validate")[0], 0)
        st = json.loads((self.d / "PROJECT_STATUS.json").read_text())
        self.assertTrue(st["facts"]["concept_approved"])
        self.assertFalse(st["facts"]["screenplay_approved"])
        self.assertTrue(all(g["state"] == "NOT_STARTED" for g in st["gates"].values()))

    def test_init_never_overwrites_and_validates_id(self):
        code, _ = run("init", "--id", "TEST-01", "--title", "x", projects=self.P)
        self.assertEqual(code, 2)
        code, _ = run("init", "--id", "bad id", "--title", "x", projects=self.P)
        self.assertEqual(code, 2)


class ApprovalEnforcement(WorkflowBase):
    def test_placeholder_blocks_submit(self):
        code, out = self.r("submit", "GATE-01")
        self.assertEqual(code, 2)
        self.assertIn("PLACEHOLDER_REMAINS", out)

    def test_cannot_approve_without_submit(self):
        code, out = self.r("approve", "GATE-01", "--by", "Human")
        self.assertEqual(code, 2)
        self.assertIn("submit first", out)

    def test_order_enforced(self):
        self.fill("STORY_TREATMENT.md")
        code, out = self.r("submit", "GATE-02")
        self.assertEqual(code, 2)
        self.assertIn("ORDER_VIOLATION", out)

    def test_approve_requires_human_name(self):
        self.fill("HISTORICAL_BIBLE.md")
        self.assertEqual(self.sub("GATE-01")[0], 0)
        self.assertEqual(self.r("approve", "GATE-01")[0], 2)
        self.assertEqual(self.r("approve", "GATE-01", "--by", "Human")[0], 0)

    def test_change_after_submit_blocks_approval(self):
        self.fill("HISTORICAL_BIBLE.md")
        self.sub("GATE-01")
        self.fill("HISTORICAL_BIBLE.md", "# changed after submit\n")
        code, out = self.r("approve", "GATE-01", "--by", "Human")
        self.assertEqual(code, 2)
        self.assertIn("CHANGED_SINCE_SUBMIT", out)

    def test_lock_detects_silent_overwrite_and_revise_reopens(self):
        self.fill("HISTORICAL_BIBLE.md")
        self.sub("GATE-01")
        self.r("approve", "GATE-01", "--by", "Human")
        self.assertEqual(self.r("validate")[0], 0, self.r("validate")[1])
        self.fill("HISTORICAL_BIBLE.md", "# silently overwritten\n")
        code, out = self.r("validate")
        self.assertEqual(code, 1)
        self.assertIn("LOCKED_FILE_MODIFIED", out)
        # proper path: revise archives old version and unlocks
        (self.d / "HISTORICAL_BIBLE.md").write_text("# MOCK content\nDone.\n")  # restore original for archive realism
        code, _ = self.r("revise", "GATE-01", "--by", "Human", "--note", "needs sources")
        self.assertEqual(code, 0)
        self.assertTrue(any((self.d / "versions").glob("GATE-01_v1*")))
        self.fill("HISTORICAL_BIBLE.md", "# v2\n")
        self.assertEqual(self.r("validate")[0], 0)

    def test_revise_makes_downstream_stale(self):
        self.fill("HISTORICAL_BIBLE.md")
        self.fill("STORY_TREATMENT.md")
        for g in ("GATE-01", "GATE-02"):
            self.assertEqual(self.sub(g)[0], 0)
            self.assertEqual(self.r("approve", g, "--by", "H")[0], 0)
        self.r("revise", "GATE-01", "--by", "H", "--note", "x")
        st = json.loads((self.d / "PROJECT_STATUS.json").read_text())
        self.assertEqual(st["gates"]["GATE-02"]["state"], "STALE")
        self.assertEqual(self.r("validate")[0], 0)

    def test_reject_and_resubmit(self):
        self.fill("HISTORICAL_BIBLE.md")
        self.sub("GATE-01")
        self.assertEqual(self.r("reject", "GATE-01", "--by", "H")[0], 2)  # note required
        self.assertEqual(self.r("reject", "GATE-01", "--by", "H", "--note", "no")[0], 0)
        self.assertEqual(self.r("approve", "GATE-01", "--by", "H")[0], 2)
        self.assertEqual(self.sub("GATE-01")[0], 0)

    def test_stage_requires_gate(self):
        code, out = self.r("stage", "start", "story")
        self.assertEqual(code, 2)
        self.assertIn("GATE_REQUIRED", out)
        self.assertEqual(self.r("stage", "start", "research")[0], 0)
        self.assertEqual(self.r("stage", "start", "image_production")[0], 2)

    def test_stage_state_forged_in_json_is_caught(self):
        st = json.loads((self.d / "PROJECT_STATUS.json").read_text())
        st["stages"]["story"]["state"] = "IN_PROGRESS"
        (self.d / "PROJECT_STATUS.json").write_text(json.dumps(st))
        code, out = self.r("validate")
        self.assertEqual(code, 1)
        self.assertIn("STAGE_WITHOUT_GATE", out)

    def test_forged_approval_without_lock_is_caught(self):
        st = json.loads((self.d / "PROJECT_STATUS.json").read_text())
        st["gates"]["GATE-03"]["state"] = "APPROVED"
        (self.d / "PROJECT_STATUS.json").write_text(json.dumps(st))
        code, out = self.r("validate")
        self.assertEqual(code, 1)
        self.assertIn("ORDER_VIOLATION", out)

    def test_blocked_external_tool(self):
        self.assertEqual(self.r("stage", "block", "research", "--external-tool", "--reason", "no video tool")[0], 0)
        st = json.loads((self.d / "PROJECT_STATUS.json").read_text())
        self.assertEqual(st["stages"]["research"]["state"], "BLOCKED_EXTERNAL_TOOL")

    def test_approval_log_audit_trail(self):
        self.fill("HISTORICAL_BIBLE.md")
        self.sub("GATE-01")
        self.r("approve", "GATE-01", "--by", "Human", "--note", "ok")
        log = (self.d / "APPROVAL_LOG.md").read_text()
        for word in ("SUBMITTED", "APPROVED", "Human", "sha256="):
            self.assertIn(word, log)

    def test_media_gate_requires_real_video_file(self):
        # walk gates 1-5 with mock docs; GATE-06 must refuse a fake mp4 when ffprobe exists
        self.fill("media/proof_of_concept/PROOF_REPORT.md")
        self.fill("media/proof_of_concept/fake.mp4", "not a video")
        for g, files in (("GATE-01", ["HISTORICAL_BIBLE.md"]), ("GATE-02", ["STORY_TREATMENT.md"]),
                         ("GATE-03", ["SCREENPLAY.md", "reviews/SCRIPT_REVIEW.md"]),
                         ("GATE-04", ["CHARACTER_BIBLE.md", "ENVIRONMENT_BIBLE.md", "VISUAL_STYLE_GUIDE.md"])):
            for f in files:
                self.fill(f)
            self.assertEqual(self.sub(g)[0], 0)
            self.assertEqual(self.r("approve", g, "--by", "H")[0], 0)
        self.fill("SHOT_LIST.csv", studio.CSV_HEADERS["SHOT_LIST.csv"] + "\n" + self.shot_row() + "\n")
        self.assertEqual(self.sub("GATE-05")[0], 0)
        self.assertEqual(self.r("approve", "GATE-05", "--by", "H")[0], 0)
        self.accept("GATE-06")
        code, out = self.r("submit", "GATE-06")
        if studio.has_ffprobe():
            self.assertEqual(code, 2)
            self.assertIn("INVALID_MEDIA", out)

    @staticmethod
    def shot_row(status="PLANNED", retries="0"):
        cols = studio.CSV_HEADERS["SHOT_LIST.csv"].split(",")
        vals = {"shot_id": "SH-001-01", "scene_id": "SC-001", "seq": "1", "description": "mock", "duration_s": "4",
                "status": status, "retries": retries}
        return ",".join(vals.get(c, "") for c in cols)


class ShotListAndCost(WorkflowBase):
    def test_shot_list_validation(self):
        h = studio.CSV_HEADERS["SHOT_LIST.csv"]
        self.fill("SHOT_LIST.csv", h + "\n" + ApprovalEnforcement.shot_row() + "\n" + ApprovalEnforcement.shot_row() + "\n")
        code, out = self.r("validate")
        self.assertEqual(code, 1)
        self.assertIn("DUP_SHOT_ID", out)
        self.fill("SHOT_LIST.csv", h + "\n" + ApprovalEnforcement.shot_row(retries="9") + "\n")
        self.assertNotIn("RETRY", self.r("validate")[1])  # no fixed retry cap any more
        self.fill("SHOT_LIST.csv", h + "\n" + ApprovalEnforcement.shot_row(status="BOGUS") + "\n")
        self.assertIn("BAD_SHOT_STATUS", self.r("validate")[1])

    def test_status_columns_can_change_after_lock_planning_cannot(self):
        # emulate approved gate-05 lock directly via fingerprint API
        h = studio.CSV_HEADERS["SHOT_LIST.csv"]
        self.fill("SHOT_LIST.csv", h + "\n" + ApprovalEnforcement.shot_row() + "\n")
        rec = studio.fingerprint(self.d, "SHOT_LIST.csv")
        self.fill("SHOT_LIST.csv", h + "\n" + ApprovalEnforcement.shot_row(status="APPROVED", retries="1") + "\n")
        self.assertTrue(studio.lock_ok(self.d, "SHOT_LIST.csv", rec))
        text = (self.d / "SHOT_LIST.csv").read_text().replace("mock", "changed planning")
        (self.d / "SHOT_LIST.csv").write_text(text)
        self.assertFalse(studio.lock_ok(self.d, "SHOT_LIST.csv", rec))

    def test_cost_tracker_append_only_lock(self):
        rec = studio.fingerprint(self.d, "COST_TRACKER.csv")
        self.assertEqual(self.r("cost", "add", "--kind", "ESTIMATE", "--stage", "image_production", "--item", "x",
                                "--units", "10", "--unit-cost", "0.5")[0], 0)
        self.assertTrue(studio.lock_ok(self.d, "COST_TRACKER.csv", rec))

    def test_actual_spend_requires_authorization_and_budget(self):
        base = ["cost", "add", "--kind", "ACTUAL", "--stage", "proof_of_concept", "--item", "clip", "--units", "2",
                "--unit-cost", "1.5"]
        code, out = self.r(*base)
        self.assertEqual(code, 2)
        self.assertIn("AUTHORIZATION_REQUIRED", out)
        self.assertEqual(self.r("cost", "authorize", "--amount", "4", "--scope", "proof", "--by", "Human")[0], 0)
        self.assertEqual(self.r(*base, "--authorization", "AU-0001")[0], 0)      # 3.0 of 4.0
        code, out = self.r(*base, "--authorization", "AU-0001")                  # would be 6.0
        self.assertEqual(code, 2)
        self.assertIn("AUTHORIZATION_EXCEEDED", out)
        self.assertEqual(self.r("cost", "set-budget", "--amount", "5", "--by", "Human")[0], 0)
        code, out = self.r("cost", "authorize", "--amount", "2", "--scope", "more", "--by", "Human")
        self.assertEqual(code, 2)
        self.assertIn("OVER_APPROVED_BUDGET", out)

    def test_estimate_refuses_to_guess_rates(self):
        code, out = self.r("cost", "estimate")
        self.assertEqual(code, 0)
        self.assertIn("Not guessing", out)

    def test_unauthorized_actual_row_caught_by_validate(self):
        studio.append_csv(self.d / "COST_TRACKER.csv", studio.CSV_HEADERS["COST_TRACKER.csv"],
                          {"entry_id": "CE-0001", "date": "2026-01-01", "kind": "ACTUAL", "amount": "5"})
        self.assertIn("UNAUTHORIZED_SPEND", self.r("validate")[1])

    def test_secret_scan(self):
        self.fill("reviews/SCRIPT_REVIEW.md", "api_key = abcdefghijklmnop1234567890\n")
        self.assertIn("POSSIBLE_SECRET", self.r("validate")[1])


class TasksLogsAdaptersExport(WorkflowBase):
    def test_task_dependencies(self):
        self.assertEqual(self.r("task", "add", "--title", "a", "--stage", "research", "--owner", "x")[0], 0)
        self.assertEqual(self.r("task", "add", "--title", "b", "--stage", "research", "--owner", "x", "--depends", "TK-0001")[0], 0)
        code, out = self.r("task", "update", "--id", "TK-0002", "--status", "IN_PROGRESS")
        self.assertEqual(code, 2)
        self.assertIn("DEPENDENCY_OPEN", out)
        self.r("task", "update", "--id", "TK-0001", "--status", "DONE")
        self.assertEqual(self.r("task", "update", "--id", "TK-0002", "--status", "IN_PROGRESS")[0], 0)
        self.assertEqual(self.r("task", "add", "--title", "c", "--stage", "research", "--owner", "")[0], 2)

    def test_actual_log_requires_status(self):
        self.assertEqual(self.r("log", "--kind", "ACTUAL", "--stage", "research", "--action", "ran")[0], 2)
        self.assertEqual(self.r("log", "--kind", "ACTUAL", "--stage", "research", "--action", "ran", "--status", "SUCCESS")[0], 0)
        self.assertEqual(self.r("log", "--kind", "PLANNED", "--stage", "research", "--action", "will run")[0], 0)

    def test_adapters_never_print_env_values(self):
        os.environ["OPENAI_API_KEY"] = "sk-THISSHOULDNEVERAPPEAR1234567890"
        try:
            code, out = self.r("adapters")
        finally:
            del os.environ["OPENAI_API_KEY"]
        self.assertEqual(code, 0)
        self.assertNotIn("THISSHOULDNEVERAPPEAR", out)
        self.assertIn("CREDENTIAL_ENV_PRESENT_UNVERIFIED", out)
        self.assertNotIn("THISSHOULDNEVERAPPEAR", (self.d / "PROJECT_STATUS.json").read_text())

    def test_export_marks_draft_and_never_overwrites(self):
        code, out = self.r("export")
        self.assertEqual(code, 0)
        self.assertIn("0 approved", out)
        self.assertEqual(self.r("export")[0], 0)
        self.assertGreaterEqual(len(list((self.d / "exports").glob("export_*"))), 2)

    def test_status_is_arabic(self):
        self.assertIn("حالة الإنتاج", self.r("status")[1])
        self.assertIn("المرحلة التالية", self.r("next")[1])


if __name__ == "__main__":
    unittest.main()

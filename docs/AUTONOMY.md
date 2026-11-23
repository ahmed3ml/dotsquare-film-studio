# Autonomous production (v1.2)

## Commands
| Slash command | Engine command | Notes |
|---|---|---|
| `/studio-film "<brief>"` | `film new --id --title --runtime --brief --by --start` | creates the project, installs the default approval policy, starts the background runner |
| `/studio-film resume <ID>` | `film resume -p <ID>` | removes the pause flag, starts the runner (recovers interrupted jobs) |
| `/studio-status` | `film status -p <ID>` (+ `--verbose`) | Egyptian-Arabic summary; `film plan` shows the exact next actions |
| `/studio-pause` | `film pause` / `film stop [--now]` | pause = after the current job; stop --now = SIGKILL (safe: job re-runs) |
| `/studio-resume` | `film resume` | |
| `/studio-cost` | `film cost`, `cost estimate`, `cost summary` | agent compute (actual) vs generation estimate/actual/budget |
| `/studio-deliver` | `film deliver` | export after GATE-09 approval; local only, never publishes |
| (human) | `policy show/check/set/enable/disable/accept-scope/paid-generation/llm-budget --amount USD`, `external record`, `hold release`, `spec set`, `film unblock` | human decisions, permission-prompted |
Existing commands (`/studio-approve`, `/studio-review`, `/studio-export`, ...) are unchanged.

## How it works
1. **Authority.** `studio.py` (PROJECT_STATUS.json) remains the state machine. `orchestrator.plan()` is a pure function of
   engine state + the orchestrator's job records; every loop re-plans, so restarts and crashes lose nothing.
2. **Hand-offs.** `standards/pipeline.json` maps each stage to its creator agent, deliverables, independent reviewer and
   criteria set. When a stage's required gate is APPROVED the next stage starts automatically.
3. **Agents.** Each job runs one agent as a headless session: `claude -p --agent <role> --permission-mode acceptEdits
   --allowedTools <role tools + read-only studio.py> --disallowedTools <approve/submit/cost/generate/...> --max-budget-usd <cap>`.
   The project's PreToolUse hook still guards logs, approved files, the policy, providers and orchestrator state. Briefs and
   raw results are kept in `orchestrator/jobs/<job>/`.
4. **Review & repair.** creator job → `review open` → reviewer job writes verdict → `review close` (engine validates evidence) →
   ACCEPTED: `submit` → policy/human approval; REVISION_NEEDED: previous version archived in `versions/`, targeted revision job
   with the findings, new cycle. Three consecutive failed cycles for one target → executive-producer diagnosis
   (`reviews/diagnosis/`) → target BLOCKED; other independent work continues; a human clears it with `film unblock`.
5. **AUTO_APPROVAL** (`approval_policy.json`, enforced in `studio/autonomy.py` inside `approve`): see CLAUDE.md rule 1.
   External requirements (e.g. DEC-004 consultant) block *any* approval of their gate until a human records evidence.
   Content holds (e.g. CA-001, tag `[CA-001]` in a shot's notes) make `generate` refuse that shot until a human releases it.
6. **Media.** Per shot: keyframe (image) → review → video (image-to-video from the approved keyframe, or the first routed
   video capability) → `inspect` (frames, loudness, A/V offset) → independent review → `shot set-status APPROVED`. Audio:
   dialogue per shot + music/ambience beds, each reviewed. Provider failures need an error analysis; after 2 consecutive
   failures the prompt is revised (strategy change) before retrying; 4 → BLOCKED. Every attempt is in GENERATION_LOG.csv
   (provider, model, prompt, cost, authorization, output sha256, synthetic flag); provider decisions in journal.jsonl;
   machine-readable per-shot status: `film shots` → `orchestrator/SHOT_STATUS.json`.
7. **Edit/render.** `assembly.py` concatenates approved clips in SHOT_LIST order at the delivery spec, places dialogue,
   mixes beds, loudness-normalises the final (-16 LUFS), then `inspect` + `techcheck` + QC report + final delivery review.
8. **Recovery.** One runner per project (flock on `orchestrator/run.lock`); RUNNING jobs found at start are re-run;
   checkpoints after every step; transient errors (rate limit, overload, network) → WAITING_RATE_LIMIT with retry time.
   The runner is a detached local process (`film start`), not a chat session; it polls for human decisions while waiting.

## Verified (2026-11-13)
- `tests/` 85/85; `scripts/smoke_test.py` passes; `scripts/autonomy_e2e.py` 27/27 (mock agents + mock media): one brief →
  delivered, rendered and inspected MP4; consultant requirement and CA-001 enforced; GATE-07 never policy-approvable;
  SIGKILL of the background runner mid-job → resumed with no duplicate cycles; second runner refused; loop limit → diagnosis + block
  while other shots and sound continue.
- Real Claude Code backend (2026-11-13): one live intake job on a throwaway project ran as `executive-producer` through
  Claude Code 2.1.322 headless with the restricted tool list; output verified by the orchestrator; actual cost 0.37 USD logged;
  scope locked. Only this one job kind was run live; creator/reviewer/revision jobs use the same mechanism but were tested with
  the mock backend only.

## Not verified / limits
- No real media provider has been run (by design: no paid generation in this upgrade). Connector execution (agent calls an MCP
  tool, downloads the file, records the attempt) is implemented but untested live.
- Mock success is not production readiness: mock media is flagged synthetic everywhere.
- The orchestrator cannot prove an agent actually looked at frames; it requires evidence files (as before).
- Character reference sheets beyond per-shot keyframes, colour grading, subtitles and lip-sync are not automated.
- Unattended runs consume Claude usage; the per-job cap and optional `spending.llm_budget_usd` bound it; usage-limit errors pause
  the runner until the reset.

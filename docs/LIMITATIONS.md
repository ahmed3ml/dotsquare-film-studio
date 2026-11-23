# Incomplete / blocked / known limits

- **No media generation tool is connected or verified** → image, video and speech production are
  BLOCKED_EXTERNAL_TOOL until the client names a tool, a budget and authorises spend (Q-001, Q-002).
- Adapters are contracts + detection only; there are no vendor-specific API wrappers (deliberate: no tool assumed).
- Slash commands and skills are Markdown instructions; their quality depends on the model following them.
  The engine (studio.py) is what is mechanically enforced and tested.
- Human-approval is convention + permission prompt, not cryptographic (see ARCHITECTURE).

- Cost estimates need real per-tool rates set in PROJECT_STATUS.json `budget.rates` (never guessed).
- Subtitles, colour grading, music licensing and lip-sync are specified in skills but not automated.
- Historical research for MUTAH-PILOT: DRAFT v1 exists (HISTORICAL_BIBLE.md, 36 claims / 12 sources, TK-0001 DONE) but no
  primary text was read and the independent fact-check (TK-0002) has not run. GATE-01 is PREPARING, not submitted.
  (Corrected by audit 2026-11-12; earlier text said "empty template".)

## Quality-first upgrade: verified vs not verified
- **Not run live**: no real Agent Team, subagent session or hook invocation was executed in this build. Agent files, skills lists, team registry
  and hook configuration are verified structurally; the hook guard script is verified by piping JSON into the real CLI. The PreToolUse payload
  key for Write/Edit (`tool_input.file_path`) is taken from Claude Code convention and was not observed live (the guard also accepts `path`).
- Agent Teams are experimental and disabled; no team has ever been spawned here. Teammates do not preload skills (spawn prompts compensate).
- Image inspection relies on the reviewer agent actually opening extracted frames; the engine can only require evidence files, not prove the
  agent looked. Audio performance/intelligibility cannot be judged by the model → NOT_VERIFIED + human listening.
- Reviewer independence is name-based (creator string ≠ reviewer string) plus separate agent definitions; the engine cannot prove two names are
  different models/contexts.
- Provider `command` execution is implemented and tested with FFmpeg only; no real vendor CLI/API was integrated or called. `connector`
  providers are preflight-only by design (the agent performs the call and records the real outcome).
- Models: all 18 agents use `opus` per standards/model_policy.json. `fable` is a documented alias for subagents; which is "strongest" is a user
  decision: `python3 studio/studio.py models apply --model fable`.
- Costs: pricing for any provider is unknown until the human supplies real prices; nothing was spent.

## Live verification — audit 2026-11-12 (Claude Code 2.1.321, headless, on a disposable copy of the studio)
- VERIFIED LIVE: discovery of all 18 agents, 18 skills and 15 `/studio-*` commands; main session runs as `executive-producer`
  (settings `agent`); PreToolUse hook fires and blocks an Edit of APPROVAL_LOG.md (payload key `tool_input.file_path` confirmed);
  main session -> `historical-fact-checker` subagent invocation works, its declared skills are preloaded, and it read a file written by
  the main session (file exchange).
- The workspace is NOT trusted yet: Claude Code ignores the 14 `permissions.allow` entries in .claude/settings.json (hooks still run).
  Fix: open Claude Code interactively in this folder once and accept the trust dialog.
- Hook guard fails OPEN on malformed stdin JSON (exit 0). Acceptable (Claude Code always sends JSON), noted for completeness.
- Settings, hook and default agent apply only when Claude Code is started with this folder as the project root (not from a parent folder).
- 2026-11-12: studio moved from african-well/ai-film-studio to ~/Desktop/web/DotSquare buss/AI-Film-Studio; re-verified there (tests, smoke, validate, live hook + discovery).
- Still not run live: Agent Teams (disabled), any media provider, any real review cycle.

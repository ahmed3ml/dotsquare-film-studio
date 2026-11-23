# Architecture (v1.1, quality-first)

```
ai-film-studio/
├─ CLAUDE.md                    project-wide rules (loaded every session)
├─ .claude/
│  ├─ settings.json             main agent = executive-producer; permission prompts for human-only actions; PreToolUse guard hook
│  ├─ skills/<18>/SKILL.md      standards & procedures (14 craft skills + review loop, media inspection, adaptive retry, team orchestration)
│  ├─ agents/<18>.md            subagents (model from policy, skills preloaded, reviewers have no Edit tool)
│  └─ commands/studio-*.md      Arabic-facing slash commands
├─ standards/
│  ├─ criteria.json             acceptance-criteria sets + which sets each gate requires
│  ├─ teams.json                registry of the 10 teams (mode, creators, reviewers, I/O, deps, review sets, fallback, spawn prompt)
│  └─ model_policy.json         single source for agent models ("strongest")
├─ studio/studio.py + ext.py    workflow engine (gates, stages, tasks, locks, reviews, authorizations, attempts, inspection, guard)
├─ studio/providers.py          provider adapter layer (prompt-file | connector | command | local | mock) + routing
├─ adapters/                    capability contracts (adapters.json) and providers.example.json
├─ templates/  projects/<ID>/   document templates; one folder per film = source of truth
└─ tests/ scripts/smoke_test.py 70 unit tests; mock end-to-end run
```

## Choosing the mechanism (hybrid)
| Mechanism | Used for | Why |
|---|---|---|
| Skill | standards, procedures, deliverable formats, quality rules | reusable by any agent; no extra context isolation needed |
| Subagent | bounded research/execution, **every independent review** | isolated context, clear output, reviewer independence |
| Agent Team (experimental, off by default) | story angles, director+DP, image+video producers, parallel QA specialists | members must talk to each other / compete / coordinate |
Teams are not defined in files by Claude Code (the lead creates them at runtime; `.claude/teams` is not read), so `standards/teams.json` is
a studio registry used by `studio.py teams` and `/studio-team` to produce spawn prompts. Teammates inherit tools/model from the named agent
definition but **not preloaded skills**, so spawn prompts tell them to invoke skills first. Without the experimental flag the same roles run as
subagents. The project never enables the flag.

## Teams
| Team | Mode | Creators | Independent reviewers |
|---|---|---|---|
| Historical Research | subagents | historical-researcher | historical-fact-checker |
| Story Development | agent_team | story-developer, historical-researcher | dramaturg-reviewer, historical-fact-checker |
| Screenwriting & Script Review | hybrid | screenwriter | script-reviewer, dramaturg-reviewer |
| Film Direction & Cinematography | agent_team | film-director, cinematographer | continuity-supervisor |
| Character & Environment Design | subagents | visual-development-supervisor | historical-fact-checker, continuity-supervisor |
| Storyboard & Shot Planning | hybrid | shot-planner | continuity-supervisor, film-director |
| Image & Video Generation | agent_team | image-producer, video-producer | film-quality-reviewer, continuity-supervisor |
| Sound & Voice | subagents | sound-producer | audio-reviewer |
| Editing & Finishing | subagents | film-editor | film-quality-reviewer, audio-reviewer, continuity-supervisor |
| Independent QA | agent_team | – | all four reviewers |

## Review-and-revision loop (engine-enforced)
`review open` (target + criteria set + creator) → independent reviewer inspects, writes verdict JSON → `review close` →
ACCEPTED / REVISION_NEEDED / NEEDS_HUMAN_REVIEW → creator fixes → new round. Rules: reviewer ≠ creator; every criterion needs ≥20 chars of
concrete evidence; media criteria cannot PASS without evidence files (frames/measurements) — otherwise NOT_VERIFIED → human review
(`review human-ack`, human-only); files changed during review void it, changed after acceptance make it stale. `submit` requires accepted,
current reviews for the gate's sets; a shot is APPROVED only with an accepted `shot_media` review of the exact output file. No numeric scores.

## Economics
No fixed retry cap. Each FAILED attempt needs category + analysis; from the 2nd consecutive failure `--strategy-change` is required
(configurable; optional `adaptive_retry.hard_cap` is null by default). Every attempt lands in GENERATION_LOG.csv (provider, model, cost,
outcome, output hash, synthetic flag). Spending: `cost authorize` (human) → AU id → attempts/costs consume it; overspend refused; total
authorizations ≤ approved budget. `report economics` shows model usage, attempts, failures, provider costs, revision costs, remaining estimate.
Cost authorization is separate from gate approval (approving a gate never authorizes spend).

## Providers
Capability → route → provider (`projects/<ID>/providers.json`, example in adapters/). Types: prompt-file (default: nothing generated, stage
BLOCKED_EXTERNAL_TOOL), connector (MCP tool in-session: engine does preflight/authorization, agent runs the tool and records real results),
command (argv template, no shell, auth by env-var name), local, mock (synthetic test media, flagged). Replace a provider by editing a route.

## Gates (unchanged semantics)
Nine sequential human-approved gates; approved files sha256-locked (SHOT_LIST planning columns and append-only COST_TRACKER handled specially);
`revise` archives to `versions/` and makes downstream gates STALE. See README.

## Hooks
PreToolUse (Write|Edit) → `studio.py hook-guard`: blocks edits to engine-owned logs/status, audit artefacts, and approved locked files.
`validate` remains the backstop (hooks don't cover shell edits).

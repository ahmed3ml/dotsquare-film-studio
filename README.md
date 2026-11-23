# AI Film Production Studio (Claude Code)

A reusable, file-based film-production management system: 18 Skills, 18 Subagents, a registry of 10 production teams, Arabic slash commands,
a tested workflow engine (`studio/studio.py`) and per-film project folders. First project: **MUTAH-PILOT**.

> Nothing here generates media by itself and no paid service is assumed. The system plans, gates, tracks,
> reviews and prepares production-ready prompts; media tools must be connected and authorised separately.

## Quick start
```bash
cd ai-film-studio
claude                      # starts as the Executive Producer (settings.json: "agent": "executive-producer")
```
Then, in Arabic: `/studio-status` → `/studio-research` (first command for MUTAH-PILOT).
Requirements: Python 3.9+, optional ffmpeg/ffprobe. Tests: `python3 -m unittest discover -s tests`;
smoke test: `python3 scripts/smoke_test.py`.

## Commands (talk to the team)
| Command | Purpose |
|---|---|
| `/studio-new <ID> "<title>"` | start a new film (creates project from templates) |
| `/studio-status [ID]` | production status (Arabic) + validation |
| `/studio-approvals` | review pending approvals |
| `/studio-approve GATE-0X` | **you** approve a deliverable (locks files) |
| `/studio-revise GATE-0X <notes>` | request revisions (reopens approved gates safely) |
| `/studio-reject GATE-0X <reason>` | reject |
| `/studio-plan` | generate production plan |
| `/studio-next` | prepare the next stage |
| `/studio-cost` | estimate costs |
| `/studio-export` | export deliverables with a hash manifest (DRAFT vs APPROVED) |
| `/studio-research` | start historical research (GATE-01 work) |
| `/studio-team <team_id>` | run a production team (Agent Team if enabled, else subagents) |
| `/studio-review <GATE or shot>` | independent review loop with acceptance criteria and evidence |
| `/studio-authorize <amount> "<scope>"` | **you** grant a spending authorization (not artistic approval) |
| `/studio-economics` | models, attempts, failures, provider costs, remaining estimate |

Engine CLI (what the commands call): `python3 studio/studio.py {init,status,pending,next,stage,task,submit,approve,
revise,reject,log,changelog,cost{add,authorize,set-budget,summary,estimate},adapters,probe,validate,export,
review{criteria,open,close,human-ack,list},shot,attempt,generate,inspect,techcheck,report,upgrade,models,teams,hook-guard} [-p ID]`. `--json` on status for machines.

## The 9 gates
01 research+concept · 02 treatment · 03 screenplay · 04 characters+visual bible · 05 storyboard+shot list ·
06 proof of concept · 07 budget+execution plan · 08 rough cut · 09 final delivery.
Sequential; only a human approves; approved files are locked; changes go through `revise` (archives to `versions/`).
See docs/ARCHITECTURE.md.

## Quality-first model
Hybrid architecture: **Skills** encode standards/procedures, **Subagents** do bounded work and independent reviews, **Agent Teams**
(experimental, OFF by default) are used only where direct collaboration pays off; every team has a subagent fallback.
Every major deliverable needs an accepted independent review against explicit criteria (`standards/criteria.json`) with evidence;
the engine blocks `submit`/shot approval otherwise. Media is inspected directly (`inspect` → frames → reviewer looks); if impossible the
criterion is NOT_VERIFIED and a human must review. No fixed retry cap: adaptive retry with error analysis. Spending needs per-batch
`cost authorize`. Models follow `standards/model_policy.json` (strongest). Providers are routed in `providers.json` (replaceable).

## Agents (18)
Orchestrator: executive-producer (main). Creators: historical-researcher · story-developer · screenwriter · film-director ·
cinematographer · visual-development-supervisor · shot-planner · image-producer · video-producer · sound-producer · film-editor.
Independent reviewers: historical-fact-checker · dramaturg-reviewer · script-reviewer · film-quality-reviewer ·
continuity-supervisor · audio-reviewer. Teams: see `python3 studio/studio.py teams show`.

## Rules in one breath
Arabic to you, English in the film · no fabricated media/logs/sources · no spend without your explicit yes and a
logged per-batch authorization · no credentials in files · Prophet Muhammad is never depicted · invented dialogue is labelled
DRAMATIZATION · planned vs actual reported separately.

## Docs
docs/IMPLEMENTATION_REPORT.md (Arabic) · docs/ARCHITECTURE.md · docs/TOOLS.md · docs/LIMITATIONS.md · docs/TEST_RESULTS.md

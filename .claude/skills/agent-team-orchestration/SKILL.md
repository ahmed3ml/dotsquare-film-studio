---
name: agent-team-orchestration
description: Decide between Skills, Subagents and Agent Teams for each production task, spawn the studio teams from the registry, and fall back safely when Agent Teams (experimental) are disabled. Use when planning or launching any multi-specialist work.
---

# Agent Team Orchestration

## Choose the mechanism
- **Skill** – a procedure/standard any agent loads. Default for "how to".
- **Subagent** – bounded task with a clear output, runs in isolation, reports back; good for research, execution, reviews.
- **Agent Team** (experimental) – only when specialists must talk to each other, compare competing approaches, or work in
  coordinated parallel (story angles, director+DP, image+video producers, QA specialists).
Registry: `standards/teams.json` (`python3 studio/studio.py teams show|check`).

## Is the team feature on?
`teams check` reports ENABLED/DISABLED (env `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`, in shell or settings `env`). It is **off by
default and this project does not turn it on**; ask the human before suggesting it. When off, run the registry's fallback:
the same roles as subagents coordinated by you. Never claim a team ran if it did not.

## Launching a team (when enabled)
Use the team's `spawn_prompt` from the registry. Facts to respect: teams are created by natural language from the lead; teammates
can be spawned from `.claude/agents` definitions (tools/model applied) but **skills are not preloaded**, so tell each teammate to
invoke its skills first; one team per session, no nested teams, `/resume` doesn't restore teammates, task status can lag — check
files and `studio.py status`, not teammate claims. Creators and reviewers stay separate. Teammates never approve gates or spend.

## Always
Creator ≠ reviewer. Verify outputs on disk and via `validate`. Record work with `log` (kind ACTUAL only for what ran).

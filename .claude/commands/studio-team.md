---
description: شغّل فريق إنتاج متخصص (Agent Team إن كان مفعّلاً، وإلا وكلاء فرعيون)
argument-hint: <team_id> [PROJECT_ID]
---

Run team `$ARGUMENTS` from `standards/teams.json`.
1. `python3 studio/studio.py teams check` and `teams show`. Report in Arabic whether Agent Teams (experimental) are enabled.
2. If enabled and the team mode is agent_team/hybrid: use its `spawn_prompt`. Otherwise run the `fallback` with subagents.
   Never turn the experimental flag on yourself; if it would help, explain and let the human decide.
3. Make sure each creator has a different independent reviewer; run the review-and-revision loop to acceptance.
4. Verify outputs on disk, run `validate`, report in Arabic what ran (actual) vs planned.

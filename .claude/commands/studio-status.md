---
description: اعرض حالة الإنتاج
argument-hint: [PROJECT_ID]
---

Run `python3 studio/studio.py film status -p $ARGUMENTS` (production summary already in Egyptian Arabic) and
`python3 studio/studio.py validate -p $ARGUMENTS`. Present only: current stage, completed milestones, estimated vs actual
costs, truly blocking decisions, finished deliverables. No task IDs, JSON or review logs (details stay in the project files;
`film status --verbose` / `status` exist if the human asks for detail).

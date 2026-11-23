---
description: ابدأ فيلم كامل من طلب واحد، أو كمّل فيلم موجود (المنسّق الآلي)
argument-hint: "<film brief>"  |  resume <PROJECT_ID>
---

Arguments: $ARGUMENTS

You are the Executive Producer. This command hands the production to the autonomous orchestrator
(`python3 studio/studio.py film ...`), which runs every stage itself (separate headless agent sessions, independent
reviews, repair loops, policy approvals, generation, editing, delivery) and stops only for decisions that need a human.

1. If the arguments start with `resume`:
   run `python3 studio/studio.py film resume -p <PROJECT_ID>`, then `python3 studio/studio.py film status -p <PROJECT_ID>`.
2. Otherwise the arguments are a NEW film brief:
   - List `projects/` first; if a project already covers this film, resume it instead of creating a duplicate.
   - Derive from the brief only what it states: a project ID (UPPERCASE-WITH-DASHES, short), a working title, the runtime in
     minutes. Do not ask for preferences the brief or the project already records; the intake agent records open questions.
   - Run `python3 studio/studio.py film new --id <ID> --title "<title>" --runtime <minutes> --brief "<the brief verbatim>" --by "<human name>" --start`.
   - Then `python3 studio/studio.py film status -p <ID>`.
3. Report to the human in Egyptian Arabic, briefly: the production has started, the current stage, and anything that needs
   them. Do not show task IDs, JSON or logs. Do not approve, authorize spending, release holds or record external reviews yourself.
4. Never use permission bypass, and never start paid generation: it requires the human's budget, a cost authorization and
   `policy paid-generation --enable --authorization AU-xxxx --by <human>`.

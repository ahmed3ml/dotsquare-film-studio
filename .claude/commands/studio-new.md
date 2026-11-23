---
description: ابدأ فيلماً جديداً (إنشاء مشروع من القوالب)
argument-hint: <PROJECT_ID> "<title>"
---

Start a new film. Arguments: $ARGUMENTS
1. Interview the human in Arabic ONLY for missing brief facts (title, genre, language, runtime, protagonist, setting, style, method, status, deliverable). Do not assume budget, approvals or tools.
2. Run: `python3 studio/studio.py init --id <ID> --title "<title>" [--genre ... --runtime-minutes ... --language ... --protagonist ... --setting ... --style ...]`.
3. Fill PROJECT.md with the stated facts; unknowns stay [[TBD]] or become Open Decisions.
4. Run `python3 studio/studio.py adapters -p <ID>` and report tool availability. Then show status and the recommended first stage (Arabic).

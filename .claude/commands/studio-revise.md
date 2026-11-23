---
description: اطلب تعديلات على بوابة
argument-hint: GATE-0X <notes>
---

The human requests revisions. Arguments: $ARGUMENTS
Run `python3 studio/studio.py revise <GATE> --by "<human>" --note "<notes>"`; log it, then route the notes to the owning agent for a new version (CHANGE_LOG entry). Re-submit when done.

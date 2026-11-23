---
description: وقّف الإنتاج الآلي مؤقتاً (بعد ما الشغلانة الحالية تخلص)
argument-hint: <PROJECT_ID>
---

Run `python3 studio/studio.py film pause -p $ARGUMENTS`. The runner stops after the current job finishes (nothing is lost;
state is checkpointed). To stop immediately even mid-job: `python3 studio/studio.py film stop --now -p $ARGUMENTS`
(the interrupted job is re-run safely on resume). Tell the human in Egyptian Arabic that production is paused and how to resume (`/studio-resume`).

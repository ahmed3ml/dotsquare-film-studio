---
description: سلّم الفيلم النهائي (بعد اعتماد التسليم النهائي)
argument-hint: <PROJECT_ID>
---

Run `python3 studio/studio.py film deliver -p $ARGUMENTS`. If the final delivery gate is not approved yet, say in
Egyptian Arabic what is still missing (`film status`). On success give the human the final MP4 path and the export folder,
and state clearly whether it is real production media or synthetic test media (the result says "synthetic"). Delivery is
local only: never upload, publish or distribute anything without the human's explicit instruction.

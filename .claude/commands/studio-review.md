---
description: شغّل دورة مراجعة مستقلة بمعايير قبول وأدلة لمخرَج ما
argument-hint: <GATE-0X | shot:SH-xxx-xx> [PROJECT_ID]
---

Run the review-and-revision loop for `$ARGUMENTS`: pick the criteria set (`studio.py review criteria --set <set>`), open a cycle with the
true creator, delegate the review to an independent reviewer agent (never the creator), close with their verdict JSON, then loop revisions
until accepted. Media: use media-inspection and look at real frames. Report in Arabic: per-criterion results, failures, what is NOT_VERIFIED
and needs the human's own inspection (`review human-ack` only after the human says they inspected).

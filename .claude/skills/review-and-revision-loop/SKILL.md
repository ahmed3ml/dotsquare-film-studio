---
name: review-and-revision-loop
description: Run an independent, evidence-based review of any deliverable against explicit acceptance criteria and drive revision rounds until every criterion passes or a human must decide. Use for every major creative or technical deliverable; used by all reviewers and by the Executive Producer.
---

# Review-and-Revision Loop

Principle: a deliverable is never accepted because its creator says it works. Acceptance = every criterion in the
standards set (`standards/criteria.json`) is PASS **with evidence** from someone other than the creator. No numeric scores.

## Loop
1. **Open** (EP or creator): `python3 studio/studio.py review open --target gate:GATE-0X --set <set> --creator <agent> -p ID`
   (shots: `--target shot:SH-001-01 --set shot_media --files media/video/SH-001-01/..mp4`). Criteria are printed.
2. **Review** (a different agent): read the approved inputs, inspect the deliverable itself. For media use the
   media-inspection skill (frames + measurements) and actually look at the frames. Write a verdict JSON:
   `{"criteria": {"<ID>": {"result": "PASS|FAIL|NOT_VERIFIED", "evidence": "...>=20 chars, specific (scene/shot/timecode/quote)...", "evidence_files": ["reviews/evidence/..."]}}, "summary": "...", "findings": [{"id":"RV-001","severity":"BLOCKER|MAJOR|MINOR","where":"SC-003","issue":"...","required_fix":"..."}]}`
3. **Close**: `review close RC-xxxx --reviewer <agent> --verdict-file reviews/verdicts/RC-xxxx.json`.
   Statuses: ACCEPTED · REVISION_NEEDED (any FAIL) · NEEDS_HUMAN_REVIEW (any NOT_VERIFIED or non-independent reviewer).
4. **Revise**: creator fixes exactly the failed criteria, logs it (`changelog`), then **open a new cycle** (round n+1). Files
   changed during a review void it, and changed files after acceptance make it stale — re-review is mandatory.
5. **Human review**: for NEEDS_HUMAN_REVIEW, present in Arabic what could not be verified and why; the human inspects and the
   EP records `review human-ack RC-xxxx --by <human> --note <what was inspected>` only on the human's word.
6. Only after ACCEPTED/ACCEPTED_BY_HUMAN can `submit` run (engine-enforced). The gate itself still needs human approval.

## Rules for reviewers
Never PASS what you did not inspect (use NOT_VERIFIED and say what is missing). Quote evidence. Do not rewrite the work;
state the required fix. Media criteria require evidence files. Be adversarial about identity, anatomy, continuity and history.

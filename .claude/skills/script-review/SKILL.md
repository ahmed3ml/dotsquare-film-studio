---
name: script-review
description: Review a screenplay for historical consistency, dialogue quality, pacing, character continuity, dramatic coherence and AI-production feasibility, producing a scored review report with required fixes. Use before GATE-03.
---

# Script Review

Independent review — the reviewer must not be the author. Output: `reviews/SCRIPT_REVIEW.md`
(template `templates/SCRIPT_REVIEW.md`). Review is read-only on SCREENPLAY.md.

## Checklist (score 1–5 each, cite scene IDs)
1. **Historical** — every scene vs HISTORICAL_BIBLE.md: sequence of events/commanders, anachronisms,
   disputed items stated as fact, invented quotes not labelled, prohibited depictions.
2. **Treatment fidelity** — beats, order, runtime budget per scene vs STORY_TREATMENT.md.
3. **Dialogue** — period voice, brevity, exposition dumps, word count/speakability, `[DRAMATIZATION]` tags.
4. **Pacing** — runtime total vs PROJECT.md (±5%), scene lengths, turning point placement.
5. **Character continuity** — arcs, names/titles consistent, who-knows-what, wardrobe/state carry-over.
6. **Dramatic coherence** — clear want/obstacle/outcome per scene, protagonist centrality.
7. **Production feasibility** — number of faces, crowds, complex physical action, text-on-screen, hands,
   long single takes, anything that current AI video cannot hold consistently.

## Output
Verdict `PASS | PASS_WITH_FIXES | FAIL`, a table of findings (ID `RV-001`, scene, severity BLOCKER/MAJOR/MINOR,
issue, required fix) and a list of questions for the human. Blockers must be resolved before GATE-03 is
submitted. Do not rewrite the script yourself.

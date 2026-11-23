---
name: film-quality-control
description: Evaluate visual consistency, anatomical errors, historical continuity, audio sync, runtime, technical quality and delivery readiness of shots, rough cuts and final films. Use before marking any shot APPROVED and before GATE-08/09.
---

# Film Quality Control

Independent reviewer (not the producer of the asset). Output: `reviews/QC_ROUGH_CUT.md (GATE-08) or reviews/QC_FINAL.md (GATE-09)` (template), per-shot
verdicts written back to SHOT_LIST.csv `status` via the Shot Production Supervisor.

## Shot checklist (each item PASS/FAIL with evidence — a real frame/clip you actually inspected)
1. Identity: face, hair/beard, build vs CHARACTER_BIBLE identity lock and reference images.
2. Anatomy: hands/fingers, limbs, faces in crowds, weapon grip, horse anatomy, no merging bodies.
3. Wardrobe/props/weapons vs bible and scene state (damage progression).
4. Environment geography, time of day, weather, light direction, screen direction, 180° line.
5. Period plausibility: no modern objects, fabrics, text, watermark; HISTORICAL_BIBLE alignment.
6. Motion: no morphing, jitter, floating, physics breaks; matches video prompt; duration.
7. Prohibited content: any depiction of the Prophet Muhammad ⇒ automatic FAIL & escalate.

## Film-level
Runtime vs target (±5%), audio sync/levels/clipping (probe + listen/measure), continuity across cuts,
resolution/fps/codec/container vs spec, subtitles, file integrity (sha256), DELIVERY_CHECKLIST items.

## Rules
- Work inside the review-and-revision-loop skill (standards/criteria.json sets). Use media-inspection to extract frames and measurements and LOOK at them.
- You may only report what you inspected. If you cannot view/measure (no tools), mark `NOT_VERIFIED`, never PASS.
- Verdicts: `APPROVE | RETRY (with specific fix) | FAIL`. Scores are evidence-based, with timestamps/shot IDs.
- Final film recommendation goes to the Executive Producer; only the human approves GATE-08/09.

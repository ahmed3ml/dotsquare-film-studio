---
name: ai-video-production
description: Prepare and execute AI video-generation workflows with connected tools, motion prompts, shot references, retry tracking and quality checks. Use for the proof of concept and full shot production.
---

# AI Video Production

Adapter: `video-generation`. Prerequisites: GATE-05 (shot list) for the proof of concept (GATE-06);
GATE-07 (budget/plan) before full production.

## Pre-flight
Same as ai-image-production: inspect real tools, no assumptions, no fabricated outputs. If unavailable, write
`prompts/video/<shot_id>.md` (motion prompt, start-frame ref, duration, aspect, fps, negative) and mark
`BLOCKED_EXTERNAL_TOOL`. Obtain a human `cost authorize` (batch spending permission, separate from artistic approval) and a real-price estimate before each paid batch; run `studio.py generate` for preflight where a provider route exists.

## Proof of concept (GATE-06)
Choose 1–3 representative shots (one with the protagonist's face, one environment/crowd wide, one action) so
the human can judge identity, realism and motion. Spend only the authorised amount. Output to
`media/proof_of_concept/` + `PROOF_REPORT.md` (shots, tool, settings, attempts, costs ACTUAL, issues).

## Production
1. Image-to-video from approved keyframes where possible (best identity consistency).
2. Motion prompt: one camera move + one simple subject action; duration from SHOT_LIST.
3. Files: `media/video/<shot_id>/vNN.mp4`, never overwritten; `studio.py probe FILE` to record real
   duration/resolution/codec; log each attempt (`log`), increment `retries`.
4. **Adaptive retry** (adaptive-retry skill): no arbitrary cap; each failure gets a category + analysis, and from the 2nd
   consecutive failure the engine requires an explicit `--strategy-change`. Persistent failure → redesign the shot with the director.
5. Clips are accepted only through an independent `shot_media` review of the exact file (media-inspection evidence: frames,
   measurements). Reject identity drift, anatomy errors, anachronisms, wrong geography/light, morphing, text artefacts.
6. Report planned vs actual: shots approved / total, retries, cost vs estimate.

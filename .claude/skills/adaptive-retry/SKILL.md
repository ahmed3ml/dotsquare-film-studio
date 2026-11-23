---
name: adaptive-retry
description: Retry failed AI generations intelligently using failure categorisation, error analysis and explicit strategy changes instead of blind repetition or arbitrary caps. Use in every image, video and audio generation loop.
---

# Adaptive Retry

No fixed low retry limit exists; quality wins. But every retry must be *different and reasoned*, and every attempt is recorded
(`GENERATION_LOG.csv`) with provider, model, cost, outcome. Spending still needs a valid authorization (`cost authorize`).

## After each FAILED attempt record (`attempt record --outcome FAILED`):
`--failure-category` ∈ identity_drift · anatomy · morphing/motion · composition · lighting/time/weather · anachronism ·
text/logo artefact · prompt_misread · reference_ignored · duration/format · provider_error · policy_refusal · other
`--analysis` what exactly was wrong (frame/timecode) and the probable cause.

## Strategy ladder (change at least one thing per retry; engine requires `--strategy-change` after 2 consecutive failures)
1 tighten prompt (verbatim identity/environment prompts, explicit negatives) → 2 add/replace reference images → 3 simplify the
action or camera move, shorten duration → 4 change the angle/framing (wide/silhouette) → 5 switch model or provider →
6 generate keyframes first, then image-to-video → 7 redesign the shot with the director (SHOT_LIST revise) → 8 escalate to the
human (the shot may need a different staging). Mark persistent failures `FAILED` and report; do not keep burning credits.

## Cost awareness (transparent, not restrictive)
Show the human the running totals (`studio.py report economics`): attempts, failures, failed cost, revision cost. If the next
attempt would exceed the batch authorization, stop and ask. Cost approval is never artistic approval.

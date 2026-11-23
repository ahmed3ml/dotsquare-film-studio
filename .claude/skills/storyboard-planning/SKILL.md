---
name: storyboard-planning
description: Break approved scenes into uniquely identified shots with framing, camera movement, timing, action, transitions and storyboard instructions, producing SHOT_LIST.csv. Use after GATE-04 for GATE-05.
---

# Storyboard Planning

Prerequisite: GATE-04 APPROVED. Output: `SHOT_LIST.csv` (+ optional `prompts/storyboard/`). GATE-05.

## SHOT_LIST.csv columns (exact header)
`shot_id,scene_id,seq,description,shot_type,camera_angle,camera_movement,lens_mm,duration_s,characters,environment_id,props,time_of_day,weather,dialogue_ref,sfx_notes,music_cue,transition_in,transition_out,image_prompt,video_prompt,ref_assets,status,retries,notes`

## Rules
1. Shot IDs `SH-<scene#>-<nn>` (SH-001-01); stable, never reused. Status starts `PLANNED`.
2. Durations: 2–6 s each (AI video limit); scene total must equal the screenplay TARGET ±1 s; film total ±5%
   of runtime. Compute and report totals.
3. Every shot references existing CH/ENV/PR IDs from the approved bibles — validate IDs.
4. `image_prompt` / `video_prompt` follow the Visual Style Guide grammar and reuse identity/environment prompts
   verbatim. Prompts are English. Video prompt = camera movement + one simple action.
5. Maintain continuity columns (time, weather, screen direction in notes). Mention the 180° line.
6. No generated-asset claims: `ref_assets` only lists real `AS-` IDs.
7. Also supply an Arabic summary table for the human review.

Shot statuses: `PLANNED → PROMPT_READY → IMAGE_DONE → VIDEO_DONE → IN_REVIEW → APPROVED | RETRY | FAILED | BLOCKED_EXTERNAL_TOOL`.

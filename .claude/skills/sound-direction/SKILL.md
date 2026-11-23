---
name: sound-direction
description: Manage English dialogue, voice direction, narration, sound effects, ambience, music requirements and audio asset specifications. Use to plan and produce the film's audio.
---

# Sound Direction

Adapters: `speech-generation`, `audio-processing`. Spend rules as in ai-image-production.

## Deliverable: `reviews/SOUND_PLAN.md` (create), then assets under `media/audio/`
1. **Dialogue sheet** — every line from the approved SCREENPLAY: scene, character, text (verbatim),
   delivery notes (emotion, pace, volume), target duration, voice ID. Lines stay English.
2. **Voice casting** — per character voice profile (age, register, accent intent). Use only voices the
   connected tool genuinely offers; no cloning of real persons without explicit written consent.
3. **Narration/title cards** if in the script.
4. **Ambience & SFX list** per scene/shot: wind, hooves, steel, cloth, crowd, distant horns; foley notes.
   Period plausibility: no modern engines, no gunpowder.
5. **Music cue sheet** — cue ID, scene, start/end, mood, instrumentation (period-evocative), tempo, intensity
   arc; rights status (`GENERATED | LICENSED | PUBLIC_DOMAIN | TBD`). Do not use copyrighted music.
6. **Technical spec** — 48 kHz, 24-bit WAV masters, dialogue −16 LUFS-ish integrated target for delivery
   mix (confirm client spec), peak ≤ −1 dBTP, stems: DX / SFX / AMB / MUS.
7. Sync: dialogue timings must fit shot durations; flag lines that cannot fit; note lip-sync approach
   (voice-over off-screen vs lip-sync tool).
8. If speech tools are unavailable: produce the dialogue sheet and recording script, mark
   `BLOCKED_EXTERNAL_TOOL`; do not fabricate audio. Verify real files with `studio.py probe`.

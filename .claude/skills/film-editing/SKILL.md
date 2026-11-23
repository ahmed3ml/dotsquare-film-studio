---
name: film-editing
description: Create editing plans and, where local tools such as FFmpeg are available, assemble approved footage, audio, transitions and final deliverables. Use for the rough cut (GATE-08) and final export.
---

# Film Editing

Adapters: `ffmpeg`, `video-editing`, `audio-processing`, `media-inspection`. Prerequisite for assembly:
approved shots exist (SHOT_LIST status APPROVED) and GATE-07 is approved.

## Editing plan — `PRODUCTION_PLAN.md` § Edit (EDL)
Table: order, shot_id, source file (real path), in/out, transition, audio stems, notes. Total runtime vs target.
Pacing notes from the Director's Plan. Mark the plan PLANNED until executed.

## Assembly (only with real files)
1. `studio.py adapters` → confirm ffmpeg/ffprobe. Probe every source: `studio.py probe FILE`.
2. Normalise: same resolution/fps/pixel format (e.g. scale+pad to 1920x1080, 24 fps, yuv420p).
3. Concat via the concat demuxer from a generated `edit/concat.txt`; apply crossfades only where the plan says.
4. Mix stems, then mux: `ffmpeg -i video.mp4 -i mix.wav -c:v copy -c:a aac -b:a 256k out.mp4`.
5. Output to `media/rough_cut/rough_cut_vNN.mp4` and `media/final/<ID>_FINAL_vNN.mp4`; never overwrite;
   record sha256 and probe data in PRODUCTION_LOG.csv. See `docs/` for recipes.
6. Local processing is free but still logged. Re-probe the result: duration within ±1 s of plan.

If footage is missing, stop: report which shots are missing; never fill gaps with fake or placeholder footage
in a deliverable. Deliver subtitles (SRT) only if requested; take text from the approved screenplay.

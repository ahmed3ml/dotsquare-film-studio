---
name: media-inspection
description: Directly inspect generated images, video and audio with local tools (ffprobe/ffmpeg frame extraction, loudness and A/V offset measurement, delivery-spec checks) and report evidence or an explicit limitation. Use before reviewing any media criterion.
---

# Media Inspection

1. `python3 studio/studio.py inspect <file> -p ID [--frames 8]` → probes the file, extracts evenly spaced frames to
   `reviews/evidence/<name>_<hash>/frame_NN.jpg`, measures audio mean/max volume and audio–video duration offset, writes
   `inspection.json`.
2. **Look at the frames** with the Read tool (images are viewable). Judge: realism, composition, anatomy (hands, faces,
   limbs, horses), identity vs reference images, wardrobe/props, period plausibility, light/time/weather, continuity with
   neighbouring shots. For motion, compare consecutive frames for morphing/identity drift (increase `--frames`, use 12+ for
   action). Frames are samples: say so; unsampled moments are NOT_VERIFIED.
3. Technical delivery: `studio.py techcheck <file>` against `delivery_spec` (width/height/fps/codecs/duration). Needs the
   spec to be set by the human (Q-005).
4. Audio sync: use the measured offset; listening cannot be done by the model — mark speech-quality/performance criteria
   NOT_VERIFIED unless a transcription/analysis tool is actually available, and request human listening.
5. **Limitation protocol**: if inspection is unavailable (no ffmpeg, unreadable format, no image viewing), run no judgement:
   set the criterion NOT_VERIFIED, state the limitation, and request human review in Arabic.
6. Never cite evidence files you did not create or open.

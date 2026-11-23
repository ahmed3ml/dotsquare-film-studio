"""Deterministic edit/render with FFmpeg (local, free). Builds a rough cut or final master from the approved clips in
edit order, places each shot's approved dialogue at the shot start, lays approved music/ambience beds underneath,
and (final) loudness-normalises. Returns a manifest of exact inputs (with sha256) so renders are reproducible and a
changed input forces a re-render."""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path


class RenderError(Exception):
    pass


def _sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _ff(args):
    r = subprocess.run(["ffmpeg", "-v", "error", "-y"] + args, capture_output=True, text=True)
    if r.returncode != 0:
        raise RenderError(r.stderr.strip()[-600:])


def inputs_digest(segments, beds):
    items = [(s["shot_id"], _sha(s["video"]), s["duration"], _sha(s["audio"]) if s.get("audio") else "")
             for s in segments] + [(k, _sha(v)) for k, v in sorted(beds.items())]
    return hashlib.sha256(json.dumps(items).encode()).hexdigest()


def render(segments, beds, output, spec, final=False, workdir=None):
    """segments: [{shot_id, video, duration, audio|None}] in edit order; beds: {"MUSIC": path, "AMBIENCE": path}."""
    if not shutil.which("ffmpeg"):
        raise RenderError("ffmpeg not available")
    if not segments:
        raise RenderError("no approved segments to assemble")
    w, h = int(spec.get("width", 1280)), int(spec.get("height", 720))
    fps = float(spec.get("fps", 24))
    output = Path(output)
    work = Path(workdir or output.parent / ".render_tmp")
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    crf = "18" if final else "23"
    parts = []
    for i, s in enumerate(segments):
        d = float(s["duration"])
        seg = work / ("seg_%03d.mp4" % i)
        vf = "scale=%d:%d:force_original_aspect_ratio=decrease,pad=%d:%d:(ow-iw)/2:(oh-ih)/2,fps=%s,format=yuv420p,setsar=1" % (
            w, h, w, h, fps)
        if s.get("audio"):
            _ff(["-i", s["video"], "-i", s["audio"], "-filter_complex",
                 "[0:v]%s,trim=duration=%.3f,setpts=PTS-STARTPTS[v];[1:a]aresample=48000,apad,atrim=duration=%.3f[a]" % (vf, d, d),
                 "-map", "[v]", "-map", "[a]", "-t", "%.3f" % d, "-c:v", "libx264", "-preset", "veryfast", "-crf", crf,
                 "-c:a", "aac", "-ar", "48000", "-ac", "2", str(seg)])
        else:
            _ff(["-i", s["video"], "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-filter_complex",
                 "[0:v]%s,trim=duration=%.3f,setpts=PTS-STARTPTS[v]" % (vf, d), "-map", "[v]", "-map", "1:a",
                 "-t", "%.3f" % d, "-c:v", "libx264", "-preset", "veryfast", "-crf", crf, "-c:a", "aac", "-ar", "48000",
                 "-ac", "2", str(seg)])
        parts.append(seg)
    lst = work / "concat.txt"
    lst.write_text("".join("file '%s'\n" % p.name for p in parts))
    joined = work / "joined.mp4"
    _ff(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(joined)])
    total = sum(float(s["duration"]) for s in segments)
    output.parent.mkdir(parents=True, exist_ok=True)
    inputs = ["-i", str(joined)]
    chains = ["[0:a]volume=1.0[a0]"]
    labels = ["[a0]"]
    for n, (name, path) in enumerate(sorted(beds.items()), 1):
        inputs += ["-stream_loop", "-1", "-i", str(path)]
        vol = 0.25 if name == "MUSIC" else 0.15
        chains.append("[%d:a]aresample=48000,atrim=duration=%.3f,volume=%s[b%d]" % (n, total, vol, n))
        labels.append("[b%d]" % n)
    mix = "%samix=inputs=%d:duration=first:normalize=0" % ("".join(labels), len(labels))
    if final:
        mix += ",loudnorm=I=-16:TP=-1.5:LRA=11"
    mix += "[aout]"
    _ff(inputs + ["-filter_complex", ";".join(chains + [mix]), "-map", "0:v", "-map", "[aout]", "-c:v", "copy",
                  "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", "%.3f" % total, "-movflags", "+faststart", str(output)])
    shutil.rmtree(work, ignore_errors=True)
    return {"output": str(output), "sha256": _sha(output), "duration_s": total, "width": w, "height": h, "fps": fps,
            "final": final, "inputs_digest": inputs_digest(segments, beds),
            "segments": [{"shot_id": s["shot_id"], "video": str(s["video"]), "audio": str(s.get("audio") or "")} for s in segments],
            "beds": {k: str(v) for k, v in beds.items()}}

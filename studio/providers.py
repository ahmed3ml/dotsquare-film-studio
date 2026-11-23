"""Provider adapter layer. Capabilities are routed to providers by configuration (projects/<ID>/providers.json), so a
provider can be replaced by editing one route. Routes may differ per shot type (route.by_shot_type).

Provider types
- prompt-file : no generation; the prompt file is the deliverable and the stage is BLOCKED_EXTERNAL_TOOL.
- connector   : a tool inside a Claude session (MCP, e.g. a claude.ai connector). The engine does preflight (gates,
                holds, authorization, price); an agent session calls the tool and records the real result with
                `attempt record`. The orchestrator verifies the output file and log row afterwards.
- command     : a local/remote CLI run without a shell from an argv template (user supplied). Real execution.
- local       : local binaries (ffmpeg/ffprobe) - free.
- mock        : synthetic FFmpeg test media for tests only (flagged synthetic everywhere, never deliverable).
No provider is assumed connected; credentials are referenced by env-var NAME only.

Provider selection never uses hard-coded rankings: `rank()` only scores metrics that carry an `evidence` file inside
the project (e.g. a recorded comparison test). Unevidenced metrics are reported as UNKNOWN.
"""
import copy
import json
import os
import shutil
import subprocess
from pathlib import Path

GENERATION_CAPS = ["image-generation", "video-generation", "image-to-video", "text-to-video", "character-video",
                   "speech-generation", "sfx-generation", "music-generation"]
PROCESSING_CAPS = ["ffmpeg", "media-inspection", "video-editing", "audio-processing", "audio-mixing"]
TYPES = ["prompt-file", "connector", "command", "local", "mock"]
CAP_KIND = {"image-generation": "image", "video-generation": "video", "image-to-video": "video",
            "text-to-video": "video", "character-video": "video", "speech-generation": "audio",
            "sfx-generation": "audio", "music-generation": "audio"}
KIND_EXT = {"image": ".png", "video": ".mp4", "audio": ".wav"}
KIND_DIR = {"image": "images", "video": "video", "audio": "audio"}
CAP_TAG = {"sfx-generation": "_sfx", "music-generation": "_music", "character-video": "_char"}
PROMPT_DIR = {"image-generation": "images", "speech-generation": "audio", "sfx-generation": "audio",
              "music-generation": "audio"}
METRICS = ["cinematic_realism", "character_consistency", "motion_quality", "camera_control", "max_duration_s",
           "max_resolution", "audio_capability", "reliability"]
QUALITY_WEIGHTS = {"cinematic_realism": 3, "character_consistency": 3, "motion_quality": 2, "camera_control": 1,
                   "reliability": 2, "audio_capability": 1}

DEFAULT = {
    "schema_version": "1.1",
    "providers": {
        "prompt-file": {"type": "prompt-file", "capabilities": GENERATION_CAPS},
        "local-ffmpeg": {"type": "local", "capabilities": PROCESSING_CAPS, "requires_bins": ["ffmpeg", "ffprobe"],
                         "pricing": {}},
        "mock-ffmpeg": {"type": "mock", "synthetic": True, "capabilities": GENERATION_CAPS, "requires_bins": ["ffmpeg"],
                        "pricing": {c: {"unit": "unit", "price": 0} for c in GENERATION_CAPS},
                        "commercial_use": "NOT_APPLICABLE_TEST_ONLY"},
    },
    "routes": {c: {"provider": "prompt-file"} for c in GENERATION_CAPS},
}


class ProviderError(Exception):
    pass


def cap_kind(cap):
    if cap not in CAP_KIND:
        raise ProviderError("unknown capability %s" % cap)
    return CAP_KIND[cap]


def needs_input(cap):
    return cap == "image-to-video"


def default_prompt(cap, shot):
    sub = PROMPT_DIR.get(cap, "video")  # every video capability shares prompts/video/<SHOT>.md
    suffix = CAP_TAG.get(cap, "") if sub == "audio" else ""
    return "prompts/%s/%s%s.md" % (sub, shot, suffix)


def mock_config():
    cfg = copy.deepcopy(DEFAULT)
    cfg["routes"] = {c: {"provider": "mock-ffmpeg", "model": "mock"} for c in GENERATION_CAPS}
    cfg["routes"]["video-generation"] = {"provider": "mock-ffmpeg", "model": "mock"}
    return cfg


def load_config(pdir):
    p = Path(pdir) / "providers.json"
    if p.exists():
        cfg = json.loads(p.read_text(encoding="utf-8"))
        for c in GENERATION_CAPS:  # older configs: unknown capabilities default to prompt files
            cfg.setdefault("routes", {}).setdefault(c, {"provider": "prompt-file"})
        cfg.setdefault("providers", {}).setdefault("prompt-file", DEFAULT["providers"]["prompt-file"])
        cfg["providers"]["prompt-file"]["capabilities"] = GENERATION_CAPS
    else:
        cfg = copy.deepcopy(DEFAULT)
    validate_config(cfg)
    return cfg


def validate_config(cfg):
    for name, prov in cfg["providers"].items():
        if prov.get("type") not in TYPES:
            raise ProviderError("provider %s: bad type %r" % (name, prov.get("type")))
        if prov["type"] == "command" and not isinstance(prov.get("command"), list):
            raise ProviderError("provider %s: command must be an argv list (no shell)" % name)
        for env in prov.get("auth_env", []):
            if "=" in env or len(env) > 64:
                raise ProviderError("provider %s: auth_env must list env var NAMES only" % name)
    for cap, route in cfg["routes"].items():
        names = [route["provider"]] + list((route.get("by_shot_type") or {}).values())
        for n in names:
            pname = n if isinstance(n, str) else n.get("provider")
            if pname not in cfg["providers"]:
                raise ProviderError("route %s -> unknown provider %s" % (cap, pname))
            if cap not in cfg["providers"][pname].get("capabilities", []):
                raise ProviderError("route %s: provider %s does not declare that capability" % (cap, pname))


def resolve(cfg, capability, shot_type=None):
    route = cfg["routes"].get(capability)
    if not route:
        raise ProviderError("no route for capability %s" % capability)
    choice = route
    if shot_type and route.get("by_shot_type") and shot_type in route["by_shot_type"]:
        sub = route["by_shot_type"][shot_type]
        choice = {"provider": sub} if isinstance(sub, str) else sub
    name = choice["provider"]
    prov = cfg["providers"][name]
    return name, prov, choice.get("model") or route.get("model") or prov.get("model") or "unspecified"


def routed_type(cfg, capability):
    try:
        return resolve(cfg, capability)[1]["type"]
    except ProviderError:
        return None


def unit_price(prov, capability):
    pr = prov.get("pricing", {}).get(capability)
    if not pr or pr.get("price") is None:
        return None
    return float(pr["price"])


def estimate(prov, capability, units):
    p = unit_price(prov, capability)
    return None if p is None else round(p * float(units), 4)


def availability(prov):
    t = prov["type"]
    missing_bins = [b for b in prov.get("requires_bins", []) if not shutil.which(b)]
    if missing_bins:
        return "MISSING_LOCAL_TOOL", "missing binaries: %s" % ", ".join(missing_bins)
    missing_env = [e for e in prov.get("auth_env", []) if e not in os.environ]
    if t == "prompt-file":
        return "BLOCKED_EXTERNAL_TOOL", "prompt files only; nothing will be generated"
    if t == "connector":
        return "AGENT_VERIFIES", "tool lives in the Claude session; agent must verify it is connected"
    if t == "command":
        if missing_env:
            return "MISSING_CREDENTIAL_ENV", "env names not set: %s" % ", ".join(missing_env)
        if not shutil.which(prov["command"][0]) and not os.path.exists(prov["command"][0]):
            return "MISSING_LOCAL_TOOL", "command not found: %s" % prov["command"][0]
        return "AVAILABLE", "command provider"
    return "AVAILABLE", t


def rank(cfg, capability, pdir, phase="production"):
    """Evidence-based comparison of every provider declaring `capability`. Returns a list of dicts, best first.
    Only metrics with an existing evidence file count; nothing is scored from reputation or assumption."""
    out = []
    for name, prov in cfg["providers"].items():
        if capability not in prov.get("capabilities", []) or prov["type"] == "prompt-file":
            continue
        status, detail = availability(prov)
        prof = prov.get("profile", {})
        score, evidenced, unknown = 0.0, {}, []
        for m in METRICS:
            rec = prof.get(m)
            if isinstance(rec, dict) and rec.get("evidence") and (Path(pdir) / rec["evidence"]).exists():
                evidenced[m] = rec.get("value")
                if m in QUALITY_WEIGHTS and isinstance(rec.get("value"), (int, float)):
                    score += QUALITY_WEIGHTS[m] * float(rec["value"])
            else:
                unknown.append(m)
        problems = []
        if status not in ("AVAILABLE", "AGENT_VERIFIES"):
            problems.append(status)
        if unit_price(prov, capability) is None:
            problems.append("PRICE_UNKNOWN")
        cu = prov.get("commercial_use", "UNKNOWN")
        if phase == "production" and prov["type"] != "mock" and cu != "ALLOWED_VERIFIED":
            problems.append("COMMERCIAL_TERMS_%s" % cu)
        if prov["type"] == "mock" and phase == "production":
            problems.append("MOCK_NOT_DELIVERABLE")
        out.append({"provider": name, "type": prov["type"], "score": score, "evidenced": evidenced,
                    "unknown_metrics": unknown, "eligible": not problems, "problems": problems,
                    "price": unit_price(prov, capability)})
    out.sort(key=lambda r: (not r["eligible"], -r["score"]))
    return out


def _run_ffmpeg(args, output):
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["ffmpeg", "-v", "error", "-y"] + args + [str(output)], capture_output=True, text=True)
    if r.returncode != 0:
        raise ProviderError(r.stderr.strip()[:300])


def _mock(capability, req):
    """Deterministic synthetic media (local FFmpeg test sources). Visibly test patterns; flagged synthetic."""
    output, units = req["output"], max(float(req.get("units") or 1), 0.5)
    hue = (sum(ord(ch) for ch in str(req.get("shot_id", ""))) * 37) % 360
    kind = cap_kind(capability)
    if kind == "image":
        _run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=1", "-vf", "hue=h=%d" % hue, "-frames:v", "1"], output)
    elif capability == "image-to-video":
        _run_ffmpeg(["-loop", "1", "-t", "%.2f" % units, "-i", req["input"], "-vf",
                     "scale=1280:720,format=yuv420p,fade=t=in:st=0:d=0.3", "-r", "24", "-c:v", "libx264", "-preset", "veryfast"],
                    output)
    elif kind == "video":
        _run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=24:duration=%.2f" % units, "-vf", "hue=h=%d" % hue,
                     "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast"], output)
    elif capability == "music-generation":
        _run_ffmpeg(["-f", "lavfi", "-i", "sine=frequency=110:duration=%.2f" % units, "-f", "lavfi", "-i",
                     "sine=frequency=165:duration=%.2f" % units, "-filter_complex", "amix=inputs=2,volume=0.5", "-ar", "48000"],
                    output)
    elif capability == "sfx-generation":
        _run_ffmpeg(["-f", "lavfi", "-i", "anoisesrc=d=%.2f:c=pink:a=0.08" % units, "-ar", "48000"], output)
    else:
        _run_ffmpeg(["-f", "lavfi", "-i", "sine=frequency=%d:duration=%.2f" % (200 + hue, units), "-af",
                     "volume=0.4", "-ar", "48000"], output)


def execute(prov, capability, req):
    """Run a provider. Returns {status, output, detail, synthetic}. Never fabricates success."""
    t = prov["type"]
    if t == "prompt-file":
        return {"status": "BLOCKED_EXTERNAL_TOOL", "output": None, "synthetic": False,
                "detail": "prompt file ready: %s" % req["prompt_file"]}
    if t == "connector":
        return {"status": "AGENT_EXECUTES", "output": None, "synthetic": False,
                "detail": "Run the connected tool yourself with the prompt file, then `attempt record` the real result."}
    if t == "mock":
        _mock(capability, req)
        return {"status": "SUCCESS", "output": req["output"], "synthetic": True, "detail": "SYNTHETIC test media"}
    if t == "command":
        Path(req["output"]).parent.mkdir(parents=True, exist_ok=True)
        subs = {"prompt_file": str(req["prompt_file"]), "output": str(req["output"]), "units": str(req["units"]),
                "shot": req["shot_id"], "input": str(req.get("input") or "")}
        argv = [a.format(**subs) for a in prov["command"]]
        r = subprocess.run(argv, capture_output=True, text=True, timeout=prov.get("timeout_s", 900))
        ok = r.returncode == 0 and os.path.exists(req["output"])
        return {"status": "SUCCESS" if ok else "FAILED", "output": req["output"] if ok else None, "synthetic": False,
                "detail": (r.stderr or r.stdout).strip()[-400:]}
    raise ProviderError("provider type %s cannot execute generation" % t)

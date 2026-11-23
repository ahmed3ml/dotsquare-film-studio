# External tools to connect

Nothing paid is assumed connected. Run `python3 studio/studio.py adapters -p <ID>` and inspect the session's
tool list (MCP connectors cannot be detected by the script).

| Adapter | Needed for | Status on this machine (checked 2026-11-11) | Action |
|---|---|---|---|
| image-generation (paid) | character sheets, keyframes (GATE-04+) | not verified; no relevant env keys found | connect a tool + authorise spend |
| video-generation (paid) | proof of concept (GATE-06) & shots | not verified | connect a tool + authorise spend |
| speech-generation (paid) | English dialogue/narration | not verified | connect a tool + authorise spend |
| ffmpeg / ffprobe | editing, probing, QC | AVAILABLE (Homebrew) | – |
| media-inspection | verify real media | AVAILABLE (ffprobe) | – |
| video-editing | beyond FFmpeg scripting | via FFmpeg | optional NLE |
| audio-processing | mixing/normalising | PARTIAL (ffmpeg yes, sox no) | optional `brew install sox` |
| local-files | everything | AVAILABLE | – |

Missing paid tool ⇒ agents write prompt files under `projects/<ID>/prompts/` and the stage is
`BLOCKED_EXTERNAL_TOOL`. Never put API keys in project files; use environment variables or connector auth.

## Provider routing
Copy `adapters/providers.example.json` to `projects/<ID>/providers.json`, set routes, real prices (null = unknown, engine refuses to guess) and
auth env-var NAMES. Connector (MCP) tools: preflight with `studio.py generate ... --connector-verified`, run the tool, then `attempt record`.

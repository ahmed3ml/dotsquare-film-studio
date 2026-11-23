---
name: ai-image-production
description: Prepare and execute AI image-generation workflows with whatever tools are actually connected, maintaining consistent character and environment references, retry limits and cost control. Use for character sheets, storyboard frames and keyframes.
---

# AI Image Production

Adapter: `image-generation` (see adapters/adapters.json). Prerequisite: GATE-04 for character/environment
references; GATE-05 for shot keyframes; GATE-07 for full production spend.

## Pre-flight (every session)
1. `python3 studio/studio.py adapters -p ID` and inspect the tools actually exposed in this session (MCP tools,
   CLIs, env-var *names*). Ask the human which tool to use if several exist.
2. If none is usable: write one prompt file per shot/reference in `projects/ID/prompts/images/<id>.md`
   (prompt, negative prompt, refs, aspect, seed policy), `stage block image_production --external-tool`, and
   report in Arabic. **Do not fabricate images or logs.**
3. **Cost gate**: show the human jobs × real unit price (from the tool's own pricing, never guessed); the human grants a batch
   `cost authorize --amount --scope --by` (spending permission, not artistic approval); every paid job cites `--authorization AU-xxxx`.
   No authorization → no job. Report with `studio.py report economics`.

## Execution
- Use the canonical prompt grammar and verbatim identity/environment prompts from the bibles.
- Reference-first: generate/approve character & environment sheets before shots; pass them as references.
- Save outputs under `media/images/<shot_id>/vNN.<ext>`; never overwrite; hash each file; register `AS-xxxx`.
- Log every job in PRODUCTION_LOG.csv (`studio.py log ... --kind ACTUAL --tool NAME --cost X`) with prompt file
  path and result path; failures are logged as failures.
- **Adaptive retry** (see adaptive-retry skill): no fixed cap; every FAILED attempt is recorded with failure category + error
  analysis, and each retry changes something. Use `studio.py generate` (preflight: gate, authorization, price) or, for connector
  tools, run the tool yourself and `attempt record` the real outcome/cost.
- A shot is `APPROVED` only via `studio.py shot set-status` after an independent accepted `shot_media` review of the exact file
  (review-and-revision-loop + media-inspection). Your own success report is not acceptance.

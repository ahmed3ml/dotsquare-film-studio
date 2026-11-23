# Media integrations — status and open tasks (2026-11-13)

Routing for real production lives in `projects/<ID>/providers.json` (humans only; hook-guarded). Candidates and what was
verified are in `adapters/providers.catalog.json`. Nothing paid is routed today, so every generation capability resolves to
`prompt-file` → BLOCKED_EXTERNAL_TOOL (explicit, never faked).

| Capability | Candidate | Status | What is missing (exact) |
|---|---|---|---|
| Image keyframes | Higgsfield (claude.ai connector) | connected, read-only verified; plan **free, 10 credits** | paid plan; USD price per image for the chosen model; commercial licence of the plan; 1-shot test recorded as evidence |
| Image-to-video / character-consistent video | Higgsfield (seedance_2_5, seedance_2_0, kling3_0, minimax_h3, grok_video_v15 offered) | connected, read-only verified | same as above + per-second price; which model, by an evidence test (no ranking assumed) |
| Text-to-video | Higgsfield / HeyGen | connected, read-only verified | price, licence, test |
| English voice / narration | HeyGen (create_speech) | connected; plan **creator, 58 credits**; account is NOT under the studio owner's email | confirm account; credit price per minute; voice licence; DEC-001: never any voice for the Prophet |
| Sound effects / music | Higgsfield generate_audio (claimed) | not tested | price, licence (no copyrighted music), test |
| Adobe Firefly | Adobe connector | **not available as a generator** (only editing tools exposed) | a Firefly generation integration |
| Google Flow / Veo | — | **not connected** | an official API/CLI + credentials (env var NAME only) |
| Editing, mixing, inspection | local FFmpeg 8.1.2 | **available, tested** | — |

## To enable real generation (human steps, in order)
1. Choose plans/providers; record each real price and the commercial-use terms (save the terms page/PDF as evidence under
   `projects/<ID>/reviews/evidence/providers/`).
2. Copy the catalog entry into `projects/<ID>/providers.json`, set `pricing` (USD per unit), `commercial_use: "ALLOWED_VERIFIED"`
   and the route(s); optionally `by_shot_type` routes.
3. Run a 1-shot comparison test per capability; record scores with `evidence` paths in the provider `profile` (only evidenced
   metrics count in `rank`).
4. Approve GATE-07, `cost set-budget`, `cost authorize --amount --scope`, then
   `policy paid-generation --enable --authorization AU-xxxx --by <name>`.

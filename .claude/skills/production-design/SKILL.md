---
name: production-design
description: Develop environments, architecture, weapons, props, costumes, lighting, colour palettes and historical art-direction references. Use after GATE-03 to build ENVIRONMENT_BIBLE.md and VISUAL_STYLE_GUIDE.md for GATE-04.
---

# Production Design

Prerequisite: GATE-03 APPROVED. Outputs: `ENVIRONMENT_BIBLE.md`, `VISUAL_STYLE_GUIDE.md` (GATE-04).

## Environment bible (`ENV-001…`)
- Location, geography, orientation (where the sun/enemy/camp lies), scale, terrain, vegetation, season, weather,
  time of day per scene, historical confidence, anachronisms to avoid.
- A **Geography Map** in text: relative positions of armies, hills, wells, routes — fixed for all scenes so
  shots stay spatially consistent.
- One 40–60 word **Environment Prompt** per location, reused verbatim.
- Props/weapons/banners (`PR-001…`): form, materials, size, period sources (HC/SRC ids), who carries what.

## Visual style guide
- Target: photorealistic cinematic live-action. Specify: lens/film-look references (generic, no living artists'
  names), grain, contrast, palette (hex swatches per act), light quality per time of day, dust/atmosphere.
- Colour script across the runtime; costume palettes per faction; banner colours (verify historically).
- **Prompt grammar** — canonical order: `[shot type] [subject+identity prompt] [action] [environment prompt]
  [light/time/weather] [lens/camera] [style suffix]`, plus the global **Negative Prompt** (no modern objects,
  text, watermarks, extra limbs, cartoon look…).
- Consistency rules: seed/reference policy, aspect ratio (default 16:9), resolution, frame rate.

## Rules
Mark every period detail DOCUMENTED / PLAUSIBLE / INVENTED. Changes after GATE-04 require `revise`.

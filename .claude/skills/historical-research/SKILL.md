---
name: historical-research
description: Research historical events from reliable sources, separate established facts from disputed claims and dramatization, and maintain a source register in HISTORICAL_BIBLE.md. Use before story development of any historical film.
---

# Historical Research

Output: `HISTORICAL_BIBLE.md` (GATE-01 deliverable, together with the concept in PROJECT.md).

## Method
1. List research questions first (sequence of events, people, geography, dates, forces, weapons, dress, language,
   religion/culture, aftermath). Put them in the bible's *Research Questions* table.
2. Prefer: primary/early sources (with their transmission chain caveats), then peer-reviewed or academic
   histories, then reputable encyclopedias. Use web search tools if available; otherwise state that research
   is from model memory and mark every claim `UNVERIFIED`.
3. **Source register** — every source gets an ID `SRC-001…`: author, title, date, type (PRIMARY/SECONDARY/
   TERTIARY), reliability note, URL/citation, accessed date. Never cite a source you did not actually read
   or cannot name; never invent URLs, page numbers or quotations.
4. **Classify every claim** with a stable ID `HC-001…` and one label:
   - `ESTABLISHED` — consistent across independent reliable sources.
   - `DISPUTED` — sources conflict; list the competing versions and who says what.
   - `UNKNOWN` — not recorded (e.g. exact numbers, tactics); the film may not state it as fact.
   - `DRAMATIZATION` — invented for story purposes; must be labelled wherever used.
5. **Quotes** — only include a quotation if a source attests it; give wording variants and source. All other
   speech is dramatization and goes in the *Dramatization Register*.
6. **Visual-period notes** — clothing, armour, weapons, mounts, banners, architecture, terrain, season/time,
   with confidence level. Flag anachronism risks for production-design.
7. **Depiction constraints** — record religious/cultural depiction constraints from PROJECT.md (e.g. the
   Prophet Muhammad must not be depicted) and escalate unresolved questions as Open Decisions.
8. **Uncertainty log** — troop numbers, casualties, manoeuvres, dates: give the range and the sources.

## Done when
All questions answered or marked UNKNOWN, ≥2 independent sources for core sequence claims, every claim
labelled, register complete, `validate` clean. Then Executive Producer submits GATE-01 (human approval).

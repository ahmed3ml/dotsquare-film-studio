---
name: screenplay-writing
description: Convert an approved story treatment into a professional English-language screenplay with scene headings, action, dialogue and stable scene identifiers. Use after GATE-02; never before the treatment is approved.
---

# Screenplay Writing

Prerequisite: GATE-02 APPROVED. Output: `SCREENPLAY.md` (reviewed, then GATE-03). Language: English.

## Format
- Scene block header: `### SC-001 — INT./EXT. LOCATION — TIME OF DAY` followed by a metadata line
  `ENV: ENV-001 | CAST: CH-001, CH-002 | TARGET: 00:45 | FIDELITY: HC-004`.
- Action lines in present tense, only what the camera can see/hear. Character names in CAPS on first use.
- Dialogue: `NAME` then line. Invented speech is tagged `[DRAMATIZATION]` at scene level; a line
  attested by a source carries `[SRC-xxx]` and must be verbatim as in the bible.
- Narration/title cards labelled `NARRATOR (V.O.)` / `TITLE CARD`.
- Scene IDs are stable and never renumbered after approval; insertions use `SC-004A`.

## Rules
1. Follow the treatment beat for beat; deviations need a note and go back to the human.
2. Runtime: ~1 page ≈ 1 minute is wrong for AI film; budget by TARGET seconds per scene and keep totals
   within ±5% of PROJECT.md runtime. Dialogue ≈ 2.5 words/second.
3. Dialogue must be minimal, speakable, period-plausible (no modern idiom), within AI-lip-sync limits.
4. No invented quotations presented as authentic. Do not depict the Prophet Muhammad; handle via off-frame.
5. Write for production: limit recurring faces per shot, avoid crowd-contact choreography and fine hand work.
6. Header must show `STATUS: DRAFT vN` until GATE-03; never mark FINAL yourself.

## Handoff
Run script-review (separate Script Reviewer agent), apply fixes as vN+1, record in CHANGE_LOG.md.

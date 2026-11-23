---
name: film-project-manager
description: Manage film project briefs, budgets, stages, task dependencies, approval gates, deliverables and progress reports. Use for starting a project, status, planning, approvals, cost control and exports.
---

# Film Project Manager

Owner of PROJECT.md, PROJECT_STATUS.json, APPROVAL_LOG.md, CHANGE_LOG.md, COST_TRACKER.csv,
PRODUCTION_LOG.csv, DELIVERY_CHECKLIST.md, PRODUCTION_PLAN.md. All reports to the human are in **Arabic**.

## Procedure
1. **Brief intake** — fill PROJECT.md from templates/PROJECT.md. Record only facts the client stated;
   everything else is `[[TBD]]` or an Open Decision. Never assume budget, tools, screenplay or visual approval.
2. **State** — read with `python3 studio/studio.py status -p ID`. Change state only via the CLI
   (`stage`, `task`, `submit`, `log`, `cost`). Never hand-edit PROJECT_STATUS.json.
3. **Plan** — produce PRODUCTION_PLAN.md: stages, owner agent per stage, tasks (`TK-xxxx`) with
   `depends_on`, parallel groups (only independent tasks), blockers, planned cost. Mark plan content PLANNED.
4. **Gates** — a gate moves NOT_STARTED → PREPARING → PENDING_APPROVAL (`submit`) → APPROVED / REVISION_REQUESTED /
   REJECTED. Before `submit`: `validate` passes, no `[[TBD]]`, reviewer sign-off exists. Present an Arabic
   approval request: what is being approved, files + hashes, open risks, cost impact, what happens next.
5. **Cost** — `cost estimate` before any generation; `cost add` only after human authorisation; keep
   ESTIMATE and ACTUAL rows separate; warn at 80% of approved budget; stop at 100%.
6. **Change control** — approved files are locked. Changes go through `revise` + CHANGE_LOG entry
   (`changelog`). Downstream approved gates become STALE and need re-review.
7. **Progress report (Arabic)** — current stage, gates table, blocked items (BLOCKED_EXTERNAL_TOOL / waiting
   approval), planned vs actual (tasks, cost), next recommended action + command.

## Never
Self-approve; skip gates; mark work done without a log row; report planned work as executed.

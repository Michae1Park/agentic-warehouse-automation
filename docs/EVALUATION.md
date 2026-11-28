# Evaluation

Full results. Everything runs in simulation; scripts are in `eval/`, raw results in `eval/results/`.

## Contents

- [Longer-horizon stress test](#longer-horizon-stress-test)
- [Long-horizon harness ablation](#long-horizon-harness-ablation)
- [All tests](#all-tests)

## Longer-horizon stress test

**Question:** can the agent run a whole shift on its own — a dozen or more orders, with surprises along the way — without losing track?

### Setup

One request per trial: *"Get all pending DISPATCH-1 orders out before the truck leaves."*. The robot is mocked, so only the agent's decisions are tested. Each trial is a randomized shift the agent was never tuned on (held-out). Command: `eval/agent_eval.py --scenarios stress stress_xl`.

| What the shift contains | `stress` | `stress_xl` |
|---|---|---|
| Orders to dispatch (priority 1–3, 1–2 lines of 1–3 units) | 12 | 20 |
| Orders for another dock — must be left alone | 2 | 3 |
| Orders needing a non-pickable item — must be flagged for manual handling, not dispatched | 2 | 3 |
| Stock records that turn out to point at an empty bin (the surprise) | 3 | 5 |
| Shelf stock per item | 2–3 bins × 1–3 units | 3–4 bins × 2–4 units |
| Trials | 10 seeds, temperature 0.7 | same |

### How a trial is scored

A trial **passes only if all six checks pass**, judged from the final database and the true physical state:

| # | Check | What it tests |
|---|---|---|
| 1 | **Priority** | Higher-priority orders got stock first; no order that could have been filled was left behind |
| 2 | **Unfilled flagged** | Every order that could not be filled was flagged |
| 3 | **Manual flagged** | Non-pickable orders were flagged, not dispatched |
| 4 | **Other dock** | The other dock's orders were not touched |
| 5 | **No paused task** | No task was left paused at the end |
| 6 | **Put-away** | Stock left over at the cross-dock was put back |

### Baseline (2026-11-27)

The agent as-is, before any long-horizon work. "Recipes" are hand-written step-by-step hints in the prompt.

| | `stress` with recipes | `stress` without | `stress_xl` with recipes | `stress_xl` without |
|---|---|---|---|---|
| **Pass (all 6 checks)** | **2 / 10** | 0 / 10 | 0 / 10 | 0 / 10 |
| 1 Priority | 3 | 5 | 0 | 0 |
| 2 Unfilled flagged | 10 | 8 | 7 | 3 |
| 3 Manual flagged | 7 | 8 | 10 | 9 |
| 4 Other dock | 10 | 10 | 10 | 10 |
| 5 No paused task | 10 | 10 | 9 | 4 |
| 6 Put-away | 6 | 1 | 5 | 7 |
| Tool calls, mean (range) | 43 (34–59) | | 61 (21–89) | |

**What this shows**
- The simple rules (3, 4) hold. The agent keeps going for 50–90 tool calls
- **Priority (1) is the main failure.** After an empty bin is found, the agent fills a lower-priority order with stock a higher-priority one needed
- Without recipes, it often stops early: tasks left paused, orders not flagged (2, 5)
- The multi-step scenarios under [All tests](#all-tests) are the dev set the recipes were written for; this stress test is the held-out number
- Traces: [`eval/results/agent_stress/traces/`](../eval/results/agent_stress/traces/)

## Long-horizon harness ablation

**Question:** does wrapping the agent in a harness that tracks a plan, checks progress and forces replanning fix the stress-test failures, without recipes?

### The four harness components

Each one can be switched on or off (`backend/app/agent/harness.py`; run with `eval/agent_eval.py --configs ...`).

| | Component | What it does |
|---|---|---|
| H1 | **Plan registry** | The agent writes a plan (one subgoal per order or chore, each with a checkable end state) into the DB; it is shown every turn |
| H2 | **Re-anchoring** | When the context hits 85% of 64k (56k), old history is compacted and the request + plan are re-injected |
| H3 | **Checks + final review** | Marking a subgoal done runs a DB check; when the agent tries to finish, it gets one turn to close anything still open |
| H4 | **Replanning** | A failed check, a stock change, or 3 tool calls without progress forces the agent to revise the plan |

Two kinds of runs: **add one at a time** (B0 → +H1 → … → full), and **remove one at a time** from the full harness. Everything else as in the stress test: no recipes, 10 seeds, T=0.7.

### Results (2026-11-28)

Cells are trials out of 10, shown as `stress` · `stress_xl`. Checks 3 (manual) and 4 (other dock) pass in nearly every config and are left out.

| Config | **Pass** | 1 Priority | 2 Unfilled flagged | 5 No paused task | 6 Put-away | Tool calls, mean | Peak context, mean |
|---|---|---|---|---|---|---|---|
| B0 no harness (rerun) | **0 · 0** | 5 · 1 | 7 · 3 | 7 · 4 | 3 · 5 | 39 · 54 | 9k · 12k |
| + H1 | **0 · 0** | 6 · 1 | 9 · 5 | 9 · 5 | 1 · 4 | 70 · 110 | 15k · 20k |
| + H1 H2 | **0 · 0** | 3 · 0 | 9 · 8 | 9 · 8 | 2 · 4 | 60 · 96 | 13k · 21k |
| + H1 H2 H3 | **0 · 0** | 5 · 2 | 9 · 8 | 9 · 8 | 2 · 5 | 61 · 105 | 14k · 22k |
| **Full (H1–H4)** | **0 · 0** | 4 · 1 | **10 · 10** | **10 · 10** | 2 · 4 | 76 · 140 | 19k · 32k |
| Full − H1 | **0 · 0** | 4 · 0 | 6 · 2 | 8 · 6 | 3 · 5 | 35 · 51 | 9k · 13k |
| Full − H2 | **0 · 0** | 3 · 0 | 10 · 6 | 10 · 6 | 2 · 3 | 94 · 107 | 20k · 28k |
| Full − H3 | **0 · 0** | 2 · 1 | 8 · 9 | 8 · 10 | 2 · 1 | 69 · 126 | 18k · 29k |
| Full − H4 | **1** · 0 | **8** · 0 | 8 · 8 | 8 · 8 | 2 · 6 | 61 · 94 | 14k · 20k |

**What this shows**
- **No config fixes the stress test.** One pass in 180 trials (Full − H4 on `stress`)
- **The harness fixes follow-through.** Full harness: every unfilled order flagged and no task left paused in 20/20 trials (baseline 10/20 and 11/20). H1 (the plan) gives most of this; removing it drops back to baseline
- **It does not fix priority or put-away**, the two checks that need a correct allocation across orders. Being told about a conflict is not enough: the agent still fills lower-priority orders first
- **H2 never fired:** peak context stayed under 39k, below the 56k trigger, so H2 differences are run-to-run noise (about ±3 per check at 10 seeds)
- **Cost:** the harness roughly doubles tool calls (39 → 76, 54 → 140), mostly plan updates
- 3 of 180 trials did not settle within 900 s and count as failures
- **Next:** a deterministic allocator tool for priority (`docs/ACTION_ITEMS.md`)
- Traces: [`eval/results/agent_harness/traces/`](../eval/results/agent_harness/traces/)

## All tests

Simulation only, compared against a scripted baseline.

| Test | Result | Baseline | Speed | Setup |
|---|---|---|---|---|
| **Robustness under faults** | • **100%** orders filled<br>• **87%** inventory accuracy | Without recovery: 50% filled, 81% accuracy | 84s / order | • 24 single-order requests, mock robot<br>• 15–20% fault rates |
| **Shelf-bin perception** | • **100%** presence<br>• **92–96%** exact count<br>• **36 / 36** bad records caught | — | 1.5s / bin | 72 bins |
| **Language grounding** | **94%** correct item | — | 1.2s / query | 306 queries |
| **Tote audit** | • **6 / 6** exact<br>• **2 / 2** mis-packs caught | — | 1.4s / tote | 8 totes |
| **Multi-step planning (dev set)** | • **16 / 20** multi-step scenarios solved end to end<br>• End-of-shift: **4 / 5** at ~25 tool calls (up to 28), with a mid-run re-plan | • Without recipes: 9 / 20<br>• End-of-shift: 0 / 5 | — | • 4 scenarios × 5 trials<br>• Temperature 0.7 |

### Test notes

| Test | Notes |
|---|---|
| **Robustness** | • `eval/run_eval.py`: 24 orders across 3 randomized setups, each sent as its own request<br>• Faults: 15% of picks fail, 10% of drives are blocked, 20% of stock records point at an empty bin<br>• Measures the full system: the executor retries picks and replans paths, the agent decides on missing stock<br>• Accuracy stays below 100% because empty bins the robot never visits go undiscovered<br>• 84s / order (sim time) vs ~27s without faults: tasks wait while the agent decides |
| **Perception** | • `eval/vlm_eval.py`: 72 bins across 3 randomized setups; 36 seeded with a wrong record |
| **Grounding** | • `eval/grounding_eval.py`: by name, description or attribute (*"the red can"*)<br>• 96% on the two pickable products |
| **Tote audit** | 6 packed orders + 2 mis-packed totes at the conveyor |
| **Multi-step planning** | • `eval/agent_eval.py`, mock robot: only the agent's plan and its execution are scored, from the final database state<br>• Scenarios: 6-order batch, open-ended aisle triage, competing orders, end-of-shift (5 orders, cross-dock, mid-run missing stock, surplus put-away)<br>• Per scenario, with / without recipes: batch 5/5 · 5/5, competing 5/5 · 4/5, end-of-shift 4/5 · 0/5, triage 2/5 · 0/5<br>• Failures are judgment, not crashes: skipped follow-up, refilling a just-corrected bin, one 42-call lookup loop |

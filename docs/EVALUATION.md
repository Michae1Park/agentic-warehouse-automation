# Evaluation

Full results. Everything runs in simulation; scripts are in `eval/`, raw results in `eval/results/`.

## Contents

- [Longer-horizon stress test](#longer-horizon-stress-test)
- [Long-horizon harness ablation](#long-horizon-harness-ablation)
- [All tests](#all-tests)

## Longer-horizon stress test

A held-out test of the agent alone: randomized shifts it was never tuned on, mock robot, scored from the physical truth and the final database state (`eval/agent_eval.py --scenarios stress stress_xl`).

**Baseline (2026-11-27):** the agent as-is, before any long-horizon improvements. Later versions are compared against these numbers.

| Tier | Orders | Baseline pass, with recipes | Baseline pass, without | Tool calls (mean, range) |
|---|---|---|---|---|
| `stress` | 12 + 2 other-dock | **2 / 10** | 0 / 10 | 43 (34–59) |
| `stress_xl` | 20 + 3 other-dock | 0 / 10 | 0 / 10 | 61 (21–89) |

| Parameter | `stress` | `stress_xl` |
|---|---|---|
| Orders to dispatch | 12, priority 1–3, 1–2 lines of 1–3 units | 20 |
| Orders for another dock (must be left alone) | 2 | 3 |
| Orders needing a non-pickable SKU (manual only) | 2 | 3 |
| Stock records pointing at an empty bin (found mid-run) | 3 | 5 |
| Shelf stock per SKU | 2–3 bins × 1–3 units | 3–4 bins × 2–4 units |
| Receiving stock per SKU | 0–4 units | 0–4 units |
| Seeds × trials | 10 random seeds, temperature 0.7, one request: *"dispatch everything you can before end of shift"* | same |

| Dimension | Check | `stress` with · without | `stress_xl` with · without |
|---|---|---|---|
| Dependent decisions | Priority respected, no fillable order left behind | 3/10 · 5/10 | **0/10 · 0/10** |
| Dependent decisions | Manual orders not dispatched, and flagged | 7/10 · 8/10 | 10/10 · 9/10 |
| Dependent decisions | Other dock untouched | 10/10 · 10/10 | 10/10 · 10/10 |
| Surprises / replanning | No task left paused | 10/10 · 10/10 | 9/10 · 4/10 |
| Step count / follow-through | Every unfilled order flagged | 10/10 · 8/10 | 7/10 · 3/10 |
| Step count / follow-through | Cross-dock leftovers put away | 6/10 · 1/10 | 5/10 · 7/10 |

- The agent follows simple rules and sustains 50–90 tool calls, but cannot keep a 12–20-order allocation consistent after a surprise: it fills lower-priority orders with stock a higher-priority one needed
- Without recipes it often stops after a few turns, leaving tasks paused and orders unflagged
- The multi-step scenarios in the table below are the dev set the recipes were written against; this stress test is the held-out number
- Traces for every trial: [`eval/results/agent_stress/traces/`](../eval/results/agent_stress/traces/)

## Long-horizon harness ablation

Same stress test as above, **all runs without recipes**, with the harness components (`docs/ACTION_ITEMS.md`) switched on one by one (`eval/agent_eval.py --configs ...`).

| Component | What it adds |
|---|---|
| H1 Plan registry | Agent-authored subgoal plan in the DB with typed post-conditions, shown every turn |
| H2 Re-anchoring | When the context is nearly full, compact the history and re-inject the goal and plan; resume from the plan |
| H3 Checks + final review | DB-only post-condition checks; one review of open subgoals when the agent tries to finish |
| H4 Replanning | A failed check, a stock change, or 3 tool calls without progress injects a diff; the agent must revise the plan |

Result (2026-11-28): **no config passes** (1 of 180 trials). The harness fixes follow-through but not allocation.

| Config (all without recipes) | `stress` pass | `stress_xl` pass | Priority check (S · XL) | No task left paused (S · XL) | Every unfilled order flagged (S · XL) | Tool calls, mean (S · XL) | Peak context, mean (S · XL) | Plan revisions / trial | Replan triggers / trial |
|---|---|---|---|---|---|---|---|---|---|
| B0 baseline (rerun) | 0/10 | 0/10 | 5 · 1 | 7 · 4 | 7 · 3 | 39 · 54 | 9k · 12k | — | — |
| + H1 | 0/10 | 0/10 | 6 · 1 | 9 · 5 | 9 · 5 | 70 · 110 | 15k · 20k | 9.0 | — |
| + H1 H2 | 0/10 | 0/10 | 3 · 0 | 9 · 8 | 9 · 8 | 60 · 96 | 13k · 21k | 9.0 | — |
| + H1 H2 H3 | 0/10 | 0/10 | 5 · 2 | 9 · 8 | 9 · 8 | 61 · 105 | 14k · 22k | 9.3 | — |
| **+ H1–H4 (full)** | 0/10 | 0/10 | 4 · 1 | **10 · 10** | **10 · 10** | 76 · 140 | 19k · 32k | 9.8 | 19.0 |
| Full − H1 | 0/10 | 0/10 | 4 · 0 | 8 · 6 | 6 · 2 | 35 · 51 | 9k · 13k | — | 13.2 |
| Full − H2 | 0/10 | 0/10 | 3 · 0 | 10 · 6 | 10 · 6 | 94 · 107 | 20k · 28k | 9.2 | 16.9 |
| Full − H3 | 0/10 | 0/10 | 2 · 1 | 8 · 10 | 8 · 9 | 69 · 126 | 18k · 29k | 9.7 | 18.2 |
| Full − H4 | 1/10 | 0/10 | 8 · 0 | 8 · 8 | 8 · 8 | 61 · 94 | 14k · 20k | 8.9 | — |

- **Follow-through fixed:** with the full harness, no task is left paused and every unfilled order is flagged in 20/20 trials (baseline 11/20 and 10/20). The plan registry (H1) gives most of it; dropping H1 loses it
- **Allocation not fixed:** the priority check stays at the baseline level (0–8 of 10, within noise) in every config. Being told about a conflict is not enough; the agent still fills lower-priority orders first. This is the case for the deterministic allocator (next row of work)
- **H2 never fired:** peak context stayed under 39k, below the 85% (56k) threshold, so H1 vs H1+H2 differences are run-to-run noise. They show the noise band at 10 seeds: about ±3 per check
- **Cost:** the full harness roughly doubles tool calls (39 → 76, 54 → 140); most of the extra calls are plan updates
- Cross-dock leftover put-away stays low (2–6 of 10) in every config; no harness component targets it
- 3 of 180 trials did not settle within 900 s and count as failures
- Traces: [`eval/results/agent_harness/traces/`](../eval/results/agent_harness/traces/)

| Parameter | Value |
|---|---|
| Scenarios | `stress`, `stress_xl` (parameters above) |
| Recipes | None, for every config including the baseline |
| Seeds × temperature | 10 seeds, T=0.7 |
| Context | 64k |
| Compaction trigger | 85% of the 64k context |
| Final review | Once, when the agent tries to finish; anything still open is reported as an exception (counts as a failure) |
| Stall trigger | 3 tool calls without subgoal progress |

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

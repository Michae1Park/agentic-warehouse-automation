| Mode | Faults | Orders | Fulfillment success | Interventions / order | Avg execution (sim) | Inventory accuracy | Recovery success | Agent turn latency | Agent LLM time |
|---|---|---|---|---|---|---|---|---|---|
| scripted_baseline | nominal | 24 | 100% | 0.00 | 27.1s | 100% | — (0 faults) | — | — |
| scripted_baseline | faulty | 24 | 58% | 0.42 | 22.2s | 81% | 0% (10 faults) | — | — |
| scripted_recovery | nominal | 24 | 100% | 0.00 | 27.2s | 100% | — (0 faults) | — | — |
| scripted_recovery | faulty | 24 | 88% | 0.12 | 28.4s | 86% | 93% (14 faults) | — | — |
| agent | nominal | 24 | 100% | 0.00 | 27.3s | 100% | — (0 faults) | 10.4s | 10.3s |
| agent | faulty | 24 | 96% | 0.04 | 29.9s | 87% | 95% (21 faults) | 11.7s | 11.7s |

Simulated results against a scripted simulation baseline, not a real warehouse. Seeds [1, 2, 3], 8 generated orders per run, mock robot at 20x time scale (execution times reported in simulated seconds). Fault profile `faulty`: {'grasp_failure_rate': 0.15, 'nav_block_rate': 0.1, 'missing_stock_rate': 0.2}.

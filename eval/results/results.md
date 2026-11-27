| Mode | Faults | Orders | Fulfillment success | Interventions / order | Avg execution (sim) | Inventory accuracy | Recovery success | Agent turn latency | Agent LLM time |
|---|---|---|---|---|---|---|---|---|---|
| scripted_baseline | nominal | 24 | 100% | 0.00 | 27.3s | 100% | — (0 faults) | — | — |
| scripted_baseline | faulty | 24 | 50% | 0.50 | 23.1s | 81% | 0% (12 faults) | — | — |
| scripted_recovery | nominal | 24 | 100% | 0.00 | 27.4s | 100% | — (0 faults) | — | — |
| scripted_recovery | faulty | 24 | 88% | 0.12 | 28.2s | 86% | 93% (14 faults) | — | — |
| agent | nominal | 24 | 100% | 0.00 | 27.4s | 100% | — (0 faults) | 11.4s | 11.3s |
| agent | faulty | 24 | 100% | 0.25 | 84.2s | 87% | 100% (22 faults) | 12.8s | 12.7s |

Simulated results against a scripted simulation baseline, not a real warehouse. Seeds [1, 2, 3], 8 generated orders per run, mock robot at 20x time scale (execution times reported in simulated seconds). Fault profile `faulty`: {'grasp_failure_rate': 0.15, 'nav_block_rate': 0.1, 'missing_stock_rate': 0.2}.

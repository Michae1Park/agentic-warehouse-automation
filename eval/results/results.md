| Mode | Faults | Orders | Fulfillment success | Interventions / order | Avg execution (sim) | Inventory accuracy | Recovery success | Agent turn latency | Agent LLM time |
|---|---|---|---|---|---|---|---|---|---|
| scripted_baseline | nominal | 24 | 92% | 0.08 | 31.5s | 100% | — (0 faults) | — | — |
| scripted_baseline | faulty | 24 | 42% | 0.58 | 25.1s | 77% | 0% (12 faults) | — | — |
| scripted_recovery | nominal | 24 | 92% | 0.08 | 30.6s | 100% | — (0 faults) | — | — |
| scripted_recovery | faulty | 24 | 71% | 0.29 | 31.3s | 91% | 74% (19 faults) | — | — |
| agent | nominal | 24 | 100% | 0.00 | 28.6s | 100% | — (0 faults) | 11.0s | 10.9s |
| agent | faulty | 24 | 75% | 0.25 | 26.5s | 97% | 67% (21 faults) | 10.2s | 10.1s |

Simulated results against a scripted simulation baseline, not a real warehouse. Seeds [1, 2, 3], 8 generated orders per run, mock robot at 20x time scale (execution times reported in simulated seconds). Fault profile `faulty`: {'grasp_failure_rate': 0.15, 'nav_block_rate': 0.1, 'missing_stock_rate': 0.2}.

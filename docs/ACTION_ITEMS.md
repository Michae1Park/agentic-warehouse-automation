# Action Items

Running checklist for the Agentic Warehouse Automation demo.

## Environment

- [x] GPU: NVIDIA L40S (46GB), driver 580
- [x] Docker + Compose + NVIDIA runtime
- [x] ROS2 Jazzy installed
- [x] Python 3.12, Node 24
- [x] Isaac Sim 5.1.0 container pulled (public, no NGC login needed)
- [x] vLLM serving Qwen3.8-27B (FP8, ~34.5GB incl. KV cache) — single multimodal model for agent + vision
- [ ] Measure GPU memory budget with sim + LLM + VLM + VLA

## Step 1 — Skeleton

- [x] Docker Compose (postgres, backend, frontend)
- [x] Postgres schema: products, locations, inventory, orders, order_lines, robots, tasks, task_steps, events, exceptions
- [x] Seed data (SKUs, shelf locations, sample orders incl. #1042)
- [x] FastAPI service with REST endpoints + WebSocket event stream
- [x] React dashboard shell (orders, inventory, robot status, exceptions, chat, sim panel)
- [x] Live sync verified: DB write → trigger → NOTIFY → WebSocket → dashboard refetch
- Note: backend exposed on host port 8010 (8000 is taken on this machine), override with `BACKEND_PORT`

## Step 2 — Agent loop

- [x] Tool definitions (list_orders, get_order, find_stock, get_robot_status, plan_fulfillment, dispatch_task, get_task_status, cancel_task, list_exceptions, report_exception)
- [x] LLM tool-calling agent (OpenAI-compatible client → vLLM), conversation + latency persisted in Postgres
- [x] Persistent task state machine + executor (per-robot queue, resumes running tasks after restart)
- [x] Mock robot gateway behind `RobotGateway` protocol
- [x] Order #1042 fulfilled end-to-end against mock (12/12 steps, inventory + order updated, agent auto-reports)
- [x] Stock shortage (#1044) → agent refuses to dispatch, logs exception, proposes options
- [x] Chat panel with tool-call trace and live system events

## Step 3 — Isaac warehouse scene + navigation

- [x] Warehouse scene generated from the DB layout: shelves, one box per stocked unit, dispatch/staging pallets
- [x] Ridgeback-Franka mobile manipulator (fixed missing world anchor on the asset's articulation root)
- [ ] ROS2 bridge + Nav2 (navigation is a corridor waypoint planner inside the sim; transport is HTTP behind `RobotGateway`)
- [x] Robot gateway swaps mock → Isaac (`ROBOT_BACKEND=isaac`, compose `isaac` profile)
- [x] Live stream embedded in dashboard (MJPEG; overview / chase / inspection cameras)
- [x] Order #1042 fulfilled end-to-end in Isaac via the agent

## Step 4 — Pick and place (MoveIt2 baseline)

- [ ] MoveIt2 config for the arm (used Isaac Lula IK instead; MoveIt2 not integrated)
- [x] Scripted front grasp/place with IK retries (baseline + fallback)
- [x] Order #1042 fulfilled end-to-end in sim

## Step 5 — VLM perception

- [x] Shelf inspection / SKU identification with Qwen3.8-27B (same model as the agent), WMS-conditioned prompt
- [x] Scan-standoff inspection camera; VLM benchmark: 100% / 96% / 96% presence accuracy over 3 seeds, all record discrepancies detected
- [ ] Post-pick and post-place verification (pick verification uses grasp check, not VLM)

## Step 6 — VLA

- [x] Generate scripted demos in LeRobot format (end-effector xyz + gripper actions; 196 episodes / 7,448 frames — 4 of 200 lost to a crash-truncated write)
- [ ] Fine-tune pretrained VLA (SmolVLA base, 8k steps) — retraining on end-effector dataset
- [x] VLA as primary picker with scripted fallback (`grasp_policy=vla_with_fallback`), misses logged as recovered faults
- [ ] Grasp eval: scripted vs VLA on held-out randomized poses

## Step 7 — Failures and evaluation

- [x] Failure injection: physical world model diverges from system of record (missing stock, grasp failure, blocked path), seeded
- [x] Recovery policies: regrasp, path replan, cycle-count + reroute to alternate bin; unrecoverable → agent escalation
- [x] Scripted baseline (no LLM, no route optimization, no recovery) + scripted-with-recovery ablation
- [x] KPI harness + results table (`eval/results/results.md`; agent rows to be re-run with Qwen3.8-27B)

## Additional business workflows

- [x] Inventory inspection (cycle count task, corrects system of record)
- [x] Item relocation between bins
- [x] Stock replenishment from receiving (STAGING-1)
- [x] Agent chains replenish → wait → fulfill for a short order (#1044)
- [x] Live 2D floor map with active route overlay (fallback view next to Isaac stream)

## Step 8 — Polish

- [ ] README (business problem, rationale, architecture, enterprise integration, failures, KPIs)
- [ ] Architecture diagram
- [ ] Demo video

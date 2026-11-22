# Action Items

Running checklist for the Agentic Warehouse Automation demo.

## Environment

- [x] GPU: NVIDIA L40S (46GB), driver 580
- [x] Docker + Compose + NVIDIA runtime
- [x] ROS2 Jazzy installed
- [x] Python 3.12, Node 24
- [x] Isaac Sim 5.1.0 container pulled (public, no NGC login needed)
- [x] vLLM serving Qwen3.8-27B (FP8, ~34.5GB incl. KV cache) — single multimodal model for agent + vision
- [x] Measure GPU memory budget with sim + LLM + VLM + VLA: Isaac (high graphics) 7.0GB, VLA server 1.3GB, dev LLM (Qwen3.5-9B @0.35) 14.5GB → ~23GB. Qwen3.8-27B @0.75 (34.5GB) + Isaac + VLA ≈ 43GB, which leaves no headroom on the 46GB L40S once any other GPU job runs (likely cause of a host freeze). `scripts/demo.sh` now aborts when the budget would be exceeded

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

- [x] Warehouse scene generated from the DB layout: shelves, one box per stocked unit, dispatch conveyor, staging pallet
- [x] High-quality graphics mode (`--graphics high`): Isaac warehouse assets (pallet racking, cardboard cartons with SKU-colored labels, textured floor), ceiling lights, RTX AO/reflections, decorative background aisles. Training/eval runs keep the basic look the VLA was trained on
- [x] Outbound conveyor at dispatch: robot drops items into a tote at 0.77m, the tote rolls to an outbound pallet and a fresh tote indexes in
- [x] Dashboard camera controls (orbit / pan / zoom / reset)
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
- [x] Post-place verification: VLM audits the outbound tote against the order before it is released; mismatches raise a `tote_mismatch` exception. Post-pick verification stays a grasp check (fingertip-to-box distance)
- [x] VLM re-validated on the high-graphics scene: presence 100% / 96% / 96%, all wrong records detected (rack back panels added after open-backed racks dropped presence to ~60%); tote audit 6/6 correct orders, 2/2 mis-packs caught

## Step 6 — VLA

- [x] Generate scripted demos in LeRobot format (end-effector xyz + gripper actions; 196 episodes / 7,448 frames — 4 of 200 lost to a crash-truncated write)
- [x] Fine-tune pretrained VLA (SmolVLA base, 8k steps, 35 min) on the end-effector dataset
- [x] VLA as primary picker with scripted fallback (`grasp_policy=vla_with_fallback`), misses logged as recovered faults
- [x] Grasp eval, 50 randomized held-out trials: scripted 70% (3.4s), VLA 16% (9.0s), VLA + scripted fallback 72% (`eval/results/grasp/`)

## Step 7 — Failures and evaluation

- [x] Failure injection: physical world model diverges from system of record (missing stock, grasp failure, blocked path), seeded
- [x] Recovery policies: regrasp, path replan, cycle-count + reroute to alternate bin; unrecoverable → agent escalation
- [x] Scripted baseline (no LLM, no route optimization, no recovery) + scripted-with-recovery ablation
- [x] KPI harness + results table (`eval/results/results.md`), agent rows re-run with Qwen3.8-27B: 100% nominal / 75% faulty fulfillment

## Additional business workflows

- [x] Inventory inspection (cycle count task, corrects system of record)
- [x] Item relocation between bins
- [x] Stock replenishment from receiving (STAGING-1)
- [x] Agent chains replenish → wait → fulfill for a short order (#1044)
- [x] Live 2D floor map with active route overlay (fallback view next to Isaac stream)

## Step 8 — Polish

- [x] README (business problem, rationale, architecture, enterprise integration, failures, KPIs, results)
- [x] Architecture diagram
- [x] Multi-page dashboard: Operations, Agent and Live Simulation pages with shared live KPI bar
- [ ] Demo video (after the YCB pivot)

## Step 9 — Reliability (Isaac soak test)

- [x] Soak harness `eval/soak_eval.py`: every scoped workflow through the agent chat in Isaac, faults on, 3 seeds
- [x] Fix: replacement totes could stay hidden when two dispatch spots released within 2s
- [ ] Deploy and verify: agent tools resolve product names (agent hit its step limit on "Move 2 units of Wireless Mouse …")
- [ ] Deploy and verify: darker brown labels + prompt hint (tote audit read brown as orange)
- [ ] Known: VLM read a grey-labelled bin as a non-existent SKU-4004 (low-contrast label); superseded by the YCB pivot
- [ ] Re-run soak after fixes; cold-start `scripts/demo.sh`; one `--graphics basic` launch

## Step 10 — YCB pivot: language-conditioned grasping

Plan: [YCB Pivot Plan](https://claude.ai/artifact/DEaQ4NEy4PeF2sDDCsHHaj). Decisions: Isaac YCB objects, mixed bins with random poses, physical contact grasping, SmolVLA + diffusion policy vs scripted, descriptions and SKUs in chat, affordances learned implicitly.

- [ ] M0 Stabilize baseline: soak fixes deployed, soak re-run clean, pre-pivot demo tagged
- [ ] M1 YCB catalog + messy bins: 10–12 objects, SKU → object + description, 2–3 products per bin, random poses, a few misplaced items
- [ ] M2 Physical grasping: gripper friction/contact tuning, collision meshes, scripted grasp from true pose, per-object success over 100+ trials
- [ ] M3 Language + grounding: agent tools take descriptions, VLM locates bin and grounds the target among distractors, grounding accuracy benchmark
- [ ] M4 Demonstrations: ~500 successful language-annotated episodes across objects (LeRobot format)
- [ ] M5 Train SmolVLA (flow matching) and a diffusion policy on the same data (LLM stopped)
- [ ] M6 Evaluate + integrate: grasp success by object and clutter, wrong-object rate, end-to-end agent runs by description
- [ ] M7 README, architecture diagram and action items updated

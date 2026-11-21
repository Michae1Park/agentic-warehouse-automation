# Action Items

Running checklist for the Agentic Warehouse Automation demo.

## Environment

- [x] GPU: NVIDIA L40S (46GB), driver 580
- [x] Docker + Compose + NVIDIA runtime
- [x] ROS2 Jazzy installed
- [x] Python 3.12, Node 24
- [ ] Isaac Sim (pull `nvcr.io/nvidia/isaac-sim` container, requires NGC login)
- [x] vLLM serving Qwen3.5-9B (FP8, ~14.5GB incl. KV cache) — single multimodal model for agent + vision
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

- [ ] Warehouse scene: shelves, SKU boxes, dispatch zone
- [ ] Mobile manipulator asset
- [ ] ROS2 bridge + Nav2
- [ ] Robot gateway swaps mock → Isaac
- [ ] WebRTC stream embedded in dashboard

## Step 4 — Pick and place (MoveIt2 baseline)

- [ ] MoveIt2 config for the arm
- [ ] Scripted grasp/place
- [ ] Order #1042 fulfilled end-to-end in sim

## Step 5 — VLM perception

- [ ] Shelf inspection / SKU identification
- [ ] Post-pick and post-place verification

## Step 6 — VLA

- [ ] Generate scripted demos in LeRobot format
- [ ] Fine-tune pretrained VLA (GR00T N1.5 / SmolVLA)
- [ ] VLA as primary picker, MoveIt2 fallback

## Step 7 — Failures and evaluation

- [ ] Failure injection config (missing item, wrong item, blocked aisle, grasp failure)
- [ ] Recovery strategies
- [ ] Scripted baseline pipeline
- [ ] KPI harness + results table

## Step 8 — Polish

- [ ] README (business problem, rationale, architecture, enterprise integration, failures, KPIs)
- [ ] Architecture diagram
- [ ] Demo video

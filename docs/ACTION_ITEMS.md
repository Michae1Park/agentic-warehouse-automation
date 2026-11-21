# Action Items

Running checklist for the Agentic Warehouse Automation demo.

## Environment

- [x] GPU: NVIDIA L40S (46GB), driver 580
- [x] Docker + Compose + NVIDIA runtime
- [x] ROS2 Jazzy installed
- [x] Python 3.12, Node 24
- [ ] Isaac Sim (pull `nvcr.io/nvidia/isaac-sim` container, requires NGC login)
- [ ] vLLM serving Qwen3-30B-A3B (AWQ) + Qwen2.5-VL-7B
- [ ] Measure GPU memory budget with sim + LLM + VLM + VLA

## Step 1 — Skeleton

- [ ] Docker Compose (postgres, backend, frontend)
- [ ] Postgres schema: products, locations, inventory, orders, order_lines, robots, tasks, task_steps, events, exceptions
- [ ] Seed data (SKUs, shelf locations, sample orders incl. #1042)
- [ ] FastAPI service with REST endpoints + WebSocket event stream
- [ ] React dashboard shell (orders, inventory, robot status, exceptions, chat, sim panel)

## Step 2 — Agent loop

- [ ] Tool definitions (get_order, find_stock, reserve, plan_route, dispatch, inspect, pick, place, verify, update_inventory, raise_exception)
- [ ] LLM tool-calling agent (OpenAI-compatible client → vLLM)
- [ ] Persistent task state machine + executor
- [ ] Mock robot gateway
- [ ] Order #1042 fulfilled end-to-end against mock

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

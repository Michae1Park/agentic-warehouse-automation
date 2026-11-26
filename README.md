<p align="center">
  <h1 align="center">Agentic Warehouse Automation</h1>
  <p align="center">
    <strong>A VLM agent that runs warehouse workflows end to end: it plans, perceives, and directs a simulated mobile manipulator for the physical work</strong>
  </p>
  <p align="center">
    <a href="https://github.com/Michae1Park/agentic-warehouse-automation/actions/workflows/ci.yml"><img src="https://github.com/Michae1Park/agentic-warehouse-automation/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
    <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.12+-blue.svg" alt="Python 3.12+"></a>
    <a href="https://docs.isaacsim.omniverse.nvidia.com/"><img src="https://img.shields.io/badge/Isaac%20Sim-5.1-76b900.svg" alt="Isaac Sim 5.1"></a>
  </p>
</p>

---

<p align="center">
  <b>Author:</b> <a href="https://michae1park.github.io">Michael Park</a>
</p>

---

## Overview

A warehouse manager types *"Prepare order #1045 and move it to dispatch."* The agent reads the order from the warehouse database, reserves stock, plans the route and sends a mobile manipulator to pick it. The same model, used as a vision-language model, checks each shelf bin and audits the outbound tote before the order closes. Every physical step writes back to the database, so the chat, dashboard and live simulation always agree.

<p align="center"><br><b>Demo 1 · End-of-Shift Dispatch</b><br><sub>24 tool calls: prioritizes 5 orders, cross-docks, re-plans mid-run, puts away surplus</sub><br></p>

https://github.com/user-attachments/assets/a3f75015-5123-4154-bdc9-0be13b5c32a7

<p align="center"><br><b>Demo 2 · Order Fulfillment</b><br><sub>agent plans, robot picks from two aisles, VLM audits the outbound tote</sub><br></p>

https://github.com/user-attachments/assets/579ce229-5820-4dac-ae7e-f9722ef72d01

<p align="center"><br><b>Demo 3 · Cycle Count</b><br><sub>VLM finds misplaced stock, robot moves it back to its home bin</sub><br></p>

https://github.com/user-attachments/assets/a814f8e2-9a30-4e69-a709-75e48ea83947

<br>

### What It Does

| Workflow | Input | Execution & output |
|---|---|---|
| **End-of-shift dispatch** | *"Get all pending DISPATCH-1 orders out before the truck leaves"* | • Plans 5 orders by priority, holds what it can't fill and flags it<br>• Cross-docks a shortage from the receiving tote<br>• Re-plans when a bin turns out empty mid-run, then puts away the surplus |
| **Order fulfillment** | *"Prepare order #1045"* | • Reserves stock across bins, plans the route, picks into the robot's tote<br>• Unloads into the outbound tote on the conveyor<br>• VLM audits the tote, then the order closes |
| **Cycle count** | *"Cycle count aisle B"* | • VLM checks each bin against the WMS record<br>• Writes `cycle_count` adjustments, raises discrepancies, moves misplaced stock back to its home bin |
| **Shortage recovery** | *"Prepare order #1044"* (short on the shelf) | • Finds the gap covered by the receiving tote<br>• Cross-docks: picks the shelf stock and the received units in one trip<br>• Puts away only the surplus |
| **Exception handling** | Grasp slip, blocked path, missing stock | • Executor regrasps and replans the path<br>• Missing stock pauses the task; the agent reallocates or escalates |

<details>
<summary><b>Execution trace (Demo 1 example)</b></summary>

**Request:** *"Get all pending DISPATCH-1 orders out before the truck leaves."* (24 tool calls over 6 turns)

| Step | What happens |
|---|---|
| **1. Survey** | • `list_orders`, `get_order` ×5, `find_stock`<br>• 5 orders: #1042 and #1045 (P1), #1044 and #1046 (P2), #1040 (P3) |
| **2. Prioritize** | • P1 first: #1042 and #1045 take the shelf mustard and all the soup<br>• #1044 (3× mustard) is short on the shelf, so it is cross-docked from receiving<br>• #1040 is left short of soup: held and flagged with `report_exception`<br>• #1046 (crackers) is non-pickable: flagged for manual handling, never dispatched |
| **3. Dispatch** | `plan_fulfillment` → `dispatch_task` per order, in priority order |
| **4. Mid-run surprise** | • B-01-1 turns out empty: the executor records 0 and pauses #1044<br>• Agent calls `reallocate_pick` to take the missing unit from receiving, and the task resumes |
| **5. Surplus** | • Receiving holds 1 unreserved mustard after the change<br>• Agent plans `replenish_stock` and dispatches the put-away |
| **6. Report** | 3 orders shipped, 2 flagged with reasons, receiving empty |

</details>

<details>
<summary><b>Results</b> (fulfillment, perception, grounding, tote audit, agent reasoning)</summary>

Simulation only, compared against a scripted baseline.

| Test | Result |
|---|---|
| **Fulfillment under faults** | • **100%** of orders filled (scripted: 50%, scripted + recovery: 88%)<br>• Inventory accuracy **87%** (scripted: 81%)<br>• Cost: the agent pauses to decide on missing stock, so average execution rises to 84s from 28s (sim time)<br>• All modes **100%** without faults |
| **Shelf-bin perception** | • **100%** presence<br>• **92–96%** exact count<br>• **36 / 36** wrong records detected<br>• 1.5s per bin |
| **Language grounding** | • **94%** overall<br>• **96%** on the two pickable products<br>• 1.2s per query |
| **Tote audit** | • **6 / 6** exact<br>• **2 / 2** mis-packed totes caught<br>• 1.4s per audit |
| **Agent reasoning** | • With workflow recipes: **16 / 20** (batch 5/5, competing orders 5/5, end-of-shift 4/5, triage 2/5)<br>• Without recipes: **9 / 20** (batch 5/5, competing orders 4/5, triage 0/5, end-of-shift 0/5)<br>• End-of-shift averages 25 tool calls (up to 28), including a mid-run re-plan<br>• Failures are judgment, not crashes: skipping unrequested follow-up (surplus put-away, moving misplaced stock home), refilling a bin the count had just corrected, one 42-call lookup loop |

| Test | Setup |
|---|---|
| Fulfillment | • `eval/run_eval.py`<br>• 3 seeds × 8 orders<br>• Mock robot<br>• Faults: 15% grasp failure, 10% blocked path, 20% missing stock |
| Perception | • `eval/vlm_eval.py`<br>• 72 bins over 3 seeds<br>• 36 with a wrong record |
| Grounding | • `eval/grounding_eval.py`<br>• 306 queries<br>• By name, description or attribute (*"the red can"*) |
| Tote audit | 6 packed orders + 2 mis-packed totes at the conveyor |
| Agent reasoning | • `eval/agent_eval.py`<br>• 4 scenarios × with / without workflow recipes × 5 trials at temperature 0.7<br>• Mock robot, so only the agent's decisions are scored<br>• Scenarios: batch of 6 orders, open-ended triage, competing orders, end-of-shift with a mid-run missing-stock surprise |

</details>

<details>
<summary><b>Limitations</b></summary>

| Area | What is simplified |
|---|---|
| **Grasping** | • Scripted Lula IK from a depth pose estimate; the item attaches once the fingers close<br>• SmolVLA / diffusion policies in `vla/` are paused until retrained for the current stance |
| **System 0** | • Minimal: simulated joint drives only<br>• No force, torque, contact or slip control |
| **Products** | • 2 robot-pickable products (mustard, tomato soup)<br>• The other 10 SKUs are flagged non-pickable and routed to manual handling |
| **Navigation** | • Simulator waypoint planner: straight when clear, aisle center lines otherwise<br>• No ROS 2 / Nav2 / MoveIt 2; `RobotGateway` is where they would plug in |
| **Perception** | • One fixed inspection camera per location<br>• Cycle counts trust presence, not quantities: unseen SKU → 0, unrecorded SKU → flagged |
| **◆ Sim-assisted perception** | • The VLM outputs a 2D point on the item<br>• 3D pose estimation is sim-assisted: the point is matched to an item and its outline using simulator ground truth, then fitted to depth. A real robot would get the outline from a segmentation model such as SAM, prompted with the VLM's point<br>• Items in the robot's own tote use the simulator pose directly |
| **Faults** | Injected with seeded probabilities into a world model separate from the database |
| **Conveyor** | Moves totes kinematically out through the wall; no packing or shipping behind it |

</details>

---

## Architecture

<p align="center">
  <img src="docs/images/architecture.svg" alt="Architecture: the manager talks to the agent, which calls typed WMS and robot tools. The executor runs tasks against Isaac Sim; Postgres streams every change to the dashboard" width="100%">
</p>

### Architectural Invariants

| Invariant | What it means |
|---|---|
| **One system of record** | • Postgres holds orders, inventory, tasks and exceptions<br>• `LISTEN/NOTIFY` streams every change to the UI, so chat, dashboard and sim never disagree |
| **Typed tools, no free-form SQL** | • The agent acts only through tools that wrap transactions (`plan_fulfillment`, `dispatch_task`, …)<br>• Reservations use `SELECT … FOR UPDATE`, so two plans cannot promise the same unit<br>• Each pick updates stock, the order line and an audit row in one transaction |
| **Separate physical world** | • Faults are injected into a world model separate from the database<br>• The WMS can actually be wrong, so inventory accuracy means something |
| **Models do what they are good at** | • The VLM says *what and where* (a 2D point, a scene description), never a pose<br>• A 3D pose estimation module turns the point into a pose from depth; motion is scripted IK<br>• ◆ The point-to-object step is sim-assisted (see Limitations) |

<details>
<summary><b>System layers: what each part does</b></summary>

Layers are organized by how fast they must react (System 2 / 1 / 0).

| Layer | Rate | Part | What it does |
|---|---|---|---|
| **System 2** (reasoning) | seconds, ~0.1 Hz | **Agent** | • Tool-calling loop on Qwen3.8-27B (FP8, vLLM)<br>• Picks the workflow, plans, dispatches, replans on results |
| | | **VLM perception** | • Same model with image input<br>• Bin checks, grounding the described item, tote audit |
| | | **3D pose estimation** | • Plugged in after the VLM, which only outputs a 2D point<br>• ◆ Sim-assisted: the item and its outline come from simulator ground truth, then a depth fit gives position, yaw and height |
| **S2 → S1 interface** | per step | **Task executor** | • Deterministic state machine stored in Postgres<br>• Sequences skills, retries (regrasp, replan), pauses on missing stock, resumes after restart<br>• Reports results and findings back up |
| **System 1** (action) | 60 Hz | **Grasp & arm motion** | • Grasp planned from the estimated pose<br>• Lula IK targets, Cartesian arm trajectories |
| | | **Base motion** | • Waypoint planner, straight when no rack blocks<br>• Smooth start and stop |
| | | **Learned policy slot** | • SmolVLA and diffusion policy servers<br>• Paused until retrained for the current stance |
| **System 0** (execution) | 60 Hz sim step | **Simulated drives** | • PD position control of the Franka joints<br>• Pose-driven holonomic base<br>• No force, torque or tactile control |
| **Outside the layers** | — | **Postgres WMS** | • System of record: orders, stock, tasks, exceptions<br>• Every step writes back; changes stream to the dashboard |
| | | **Robot gateway** | • One interface: mock robot for evals, Isaac Sim over HTTP |
| | | **Isaac Sim 5.1** | • The environment: Ridgeback + Franka, physics, cameras<br>• Scene built from the WMS, seeded faults |
| | | **Dashboard** | • React + Vite: agent chat with tool trace, live sim stream, 2D floor map, orders and exceptions |

</details>

<details>
<summary><b>Design choices</b></summary>

| Choice | Why |
|---|---|
| **One model for reasoning and vision** | • Qwen3.8-27B (FP8, vLLM): open weights, native tool calling, image input<br>• The same model runs the agent and checks bins and totes |
| **No agent framework** | • One agent with typed tools, a persistent task state machine and deterministic recovery<br>• Proved more reliable than agents passing messages |
| **The VLM never outputs poses** | • It names the item and points at it (2D)<br>• A 3D pose estimation module turns that point into a pose from depth; IK plans the grasp |

</details>

<details>
<summary><b>Failure handling</b></summary>

| Fault | Handled by | Recovery | Escalation |
|---|---|---|---|
| **Grasp slip** | Executor | Regrasp, up to 3 attempts | • Task fails, reservations released<br>• Exception raised, agent reports |
| **Blocked path** | Executor | Replan the path, up to 3 attempts | Same |
| **Ordered SKU not seen at a bin** | Executor → agent | • Executor counts that SKU at the bin to 0, pauses the task, reports up<br>• Agent decides: `reallocate_pick` from other bins or receiving, then resumes | • Nothing covers it: agent cancels and escalates<br>• No agent (scripted mode): executor reallocates itself |
| **Item blocking the target** | Executor → agent | • Task pauses<br>• Agent calls `clear_blocker`, then resumes the task | Push demo paused (see the tools table) |
| **Shortage at planning** | Agent | • Receiving covers it: cross-dock in one trip<br>• Put away the surplus | Otherwise: no dispatch, exception and options |
| **Misplaced stock found by a count** | Agent | Moves it back to its home bin | — |

When a task finishes, the executor posts a `[SYSTEM EVENT]` to the conversation that created it, and the agent reports back unprompted.

</details>

<details>
<summary><b>Agent tools: the 18 typed tools the agent can call</b></summary>

| Group | Tool | What it does |
|---|---|---|
| **Orders** | `list_orders` | List orders, optionally by status |
| | `get_order` | Order with its lines (SKU, quantity, picked) |
| | `plan_fulfillment` | • Reserve stock and create a pick task with an optimized route<br>• Can also pick from the receiving tote (cross-dock)<br>• Fails with a reason when short |
| **Inventory** | `find_stock` | Every location holding a SKU or product name, with available quantity (shelves and receiving) |
| | `get_location` | Contents of a location |
| | `find_empty_bins` | Empty storage bins, optionally in one aisle |
| | `inspect_inventory` | Plan a VLM cycle count over bins or an aisle; corrects the records |
| | `relocate_stock` | Plan a move of stock between storage bins |
| | `replenish_stock` | Plan a put-away of received stock from the receiving tote to a bin |
| **Robot & tasks** | `get_robot_status` | Robot status, position, battery and tote contents |
| | `dispatch_task` | Send a planned task to the robot (asynchronous) |
| | `get_task_status` | Task status and per-step progress |
| | `wait_for_task` | Block until a task finishes, when the next step depends on it |
| | `clear_blocker` | • Splice `take_tool paddle → push_blocker → return_tool` before a blocked pick<br>• Paused: the push policy and paddle are not in the current demo scene |
| | `reallocate_pick` | • For a task paused on missing stock<br>• Reserves the gap from other bins (optionally receiving) and splices in new picks |
| | `cancel_task` | Cancel a planned or queued task and release its reservations |
| **Exceptions** | `list_exceptions` | List open, resolved or escalated exceptions |
| | `report_exception` | Record an exception that needs human attention |

</details>

---

## Quickstart

**Prerequisites:** Linux, an NVIDIA GPU, Docker with the NVIDIA runtime, and the Qwen3.8-27B-FP8 weights in the Hugging Face cache.

### 1. Configure

```bash
git clone https://github.com/Michae1Park/agentic-warehouse-automation.git && cd agentic-warehouse-automation
cp .env.example .env                        # ROBOT_BACKEND=mock for fast mode
```

### 2. Run the Isaac demo

```bash
scripts/demo.sh                             # high graphics, WebRTC, CPU-pinned, GPU budget check
```

`demo.sh` checks free GPU memory before starting:
- Budgets Isaac Sim, the LLM (`LLM_GPU_UTIL`, default 0.75) and, with a learned policy, the VLA server
- Refuses to start if other GPU jobs leave too little
- For a smaller GPU, use the lighter model commented in `.env.example`

### 3. Or run without the simulator

```bash
docker compose up -d                        # postgres, vLLM, backend, dashboard on http://localhost:5173
```

### 4. Evaluate

```bash
python3 eval/run_eval.py --seeds 1 2 3 --orders 8    # fulfillment KPIs, mock robot
python3 eval/vlm_eval.py                             # bin perception, needs the isaac profile
```

<details>
<summary><b>Once open: what you can do</b></summary>

| Area | What you can do |
|---|---|
| **Chat** | • *"Get all pending DISPATCH-1 orders out before the truck leaves"*<br>• *"Prepare order #1045"*<br>• *"Cycle count aisle B"*<br>• Every turn shows the tool trace |
| **Isaac view** | • Drag to orbit, right/shift-drag to pan, scroll to zoom<br>• Double-click or **reset view** to return to the overview |
| **Teleop** | `scripts/teleop.sh` opens a browser controller on :8020 for push demos and VLA intervention |

</details>

<details>
<summary><b>Isaac mode without <code>demo.sh</code>, and repository layout</b></summary>

```bash
echo -e "ROBOT_BACKEND=isaac\nSIM_URL=http://localhost:8020" >> .env
docker compose --profile isaac up -d        # add ISAAC_ARGS="--livestream --graphics high" for WebRTC
```

WebRTC livestream ports:
- TCP 49100 (signaling)
- UDP 47998 (media)

```
db/            schema, triggers, views and seed data
backend/app/   FastAPI: agent/, executor.py, warehouse.py, perception.py, robot/{mock,isaac}.py, sim/world.py
isaac/         Isaac Sim warehouse server (scene, robot control, conveyor, cameras, MJPEG stream)
vla/           SmolVLA and diffusion dataset conversion, training and policy server
frontend/      React dashboard
eval/          fulfillment, perception and grounding benchmarks
scripts/       demo and teleop launchers
```

</details>

<p align="center">
  <h1 align="center">Agentic Warehouse Automation</h1>
  <p align="center">
    <strong>An LLM agent that runs warehouse workflows end to end, with a VLM for perception and a simulated mobile manipulator for the physical work</strong>
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

A warehouse manager types *"Prepare order #1045 and move it to dispatch."* A **Qwen3.8-27B** agent reads the order from **Postgres** (the system of record), reserves stock, plans the route and dispatches a Ridgeback + Franka mobile manipulator in **Isaac Sim**. The same model, used as a VLM, checks each shelf bin and audits the outbound tote. Every physical step writes back to the database, so the dashboard, chat and live simulation stay in sync.

<p align="center"><br><b>Demo 1 · Deep-Shelving Pick</b><br><sub>agent + push-then-pick</sub><br></p>

<p align="center">
  <img src="docs/img/demo-deep-pick.gif" alt="Order 1045: the robot finds the last tomato soup can behind a coffee can, takes the paddle, pushes the blocker aside, picks the can and sends the audited tote down the conveyor" width="820">
</p>

<p align="center"><br><b>Demo 2 · Cycle Count</b><br><sub>VLM finds short and misplaced stock</sub><br></p>

<p align="center">
  <img src="docs/img/demo-cycle-count.gif" alt="Cycle count of an aisle: the VLM inspects each bin, flags a short bin and a misplaced SKU, the agent corrects the records and relocates the stock" width="820">
</p>

<p align="center"><br><b>Demo 3 · Shortage Recovery</b><br><sub>agent replenishes from receiving, then fulfills</sub><br></p>

<p align="center">
  <img src="docs/img/demo-shortage.gif" alt="Order 1044 is short on the shelf: the agent finds stock in the receiving tote, replenishes the bin, then fulfills the order and the VLM verifies the tote at dispatch" width="820">
</p>

<p align="center"><sub><i>Demo 1: the push is done by the scripted expert. The SmolVLA push policy is trained but paused (2/30 on held-out scenes); see <a href="docs/ACTION_ITEMS.md">action items</a>.</i></sub></p>

<br>

### What It Does

| Workflow | Input | Execution & output |
|---|---|---|
| **Order fulfillment** | *"Prepare order #1045"* | • Reserves stock, plans the route, picks into the outbound tote<br>• Clears a blocker with the paddle when the target sits behind another product<br>• VLM audits the tote, then the order closes |
| **Cycle count** | *"Cycle count aisle B"* | • VLM checks each bin against the WMS record<br>• Writes `cycle_count` adjustments, raises discrepancies, relocates misplaced stock |
| **Shortage recovery** | An order short on the shelf | • Agent does not dispatch; finds stock in the receiving tote<br>• Replenishes the bin, waits, then fulfills |
| **Exception handling** | Grasp slip, blocked path, missing stock | • Regrasp / replan / reallocate from an alternate bin<br>• Escalates with a report when recovery fails |

<details>
<summary><b>Execution trace (Demo 1 example)</b></summary>

**Request:** `"Prepare order #1045 and move it to dispatch."` (5× tomato soup)

1. **Plan**<br>`get_order` → `plan_fulfillment` reserves 4 cans at A-02-1 and the last one at C-04-1 in one `SELECT … FOR UPDATE` transaction.
2. **Dispatch**<br>`dispatch_task` hands the plan to the executor; steps are rows in `task_steps`, so a restart resumes the task.
3. **Blocked pick**<br>At C-04-1 the sim check sees a coffee can in front of the target. The executor pauses the task, raises a `blocked` exception and notifies the agent.
4. **Recovery**<br>The agent calls `clear_blocker`, which splices `take_tool paddle → push_blocker → return_tool` before the paused pick, then resumes the task.
5. **Audit and close**<br>The VLM compares the tote with the order; on a match the tote is released down the conveyor and `verify` closes the order. A `[SYSTEM EVENT]` prompts the agent to report back.

</details>

<details>
<summary><b>Results</b> (fulfillment, perception, grasping, grounding)</summary>

[Full results and limits](docs/DESIGN.md#results)

| Test | Evaluation set | Result |
|---|---|---|
| **Fulfillment (faulty)** | 24 orders, 3 seeds, injected faults | Agent 75% vs 42% scripted baseline; inventory accuracy 94% vs 77% |
| **Shelf-bin perception** | 72 bins, 25% wrong records | 96–100% presence accuracy; every wrong record detected |
| **Language grounding** | 72 queries (name, description, attribute) | 72 / 72 correct, 1.2s per query |
| **Tote audit** | 6 orders + 2 mis-packed totes | 6 / 6 correct, 2 / 2 mismatches caught |
| **Grasping** | 30 trials per policy | SmolVLA 67%, diffusion 63%, scripted 57% |

</details>

---

## Architecture

<p align="center">
  <img src="docs/images/architecture.svg" alt="Architecture: the manager talks to the agent, which calls typed WMS and robot tools. The executor runs tasks against Isaac Sim; Postgres streams every change to the dashboard" width="100%">
</p>

### Architectural Invariants

1. **One system of record**<br>Postgres holds orders, inventory, tasks and exceptions. `LISTEN/NOTIFY` streams every change to the UI, so chat, dashboard and sim never disagree.
2. **Typed tools, no free-form SQL**<br>The agent acts only through tools that wrap transactions (`plan_fulfillment`, `dispatch_task`, `clear_blocker`, …).
3. **Separate physical world**<br>Faults are injected into a world model separate from the database, so the WMS can actually be wrong and inventory accuracy means something.
4. **Models do what they are good at**<br>The VLM says *what and where* (a 2D point, a scene description); it never outputs poses. Motion is scripted IK or a learned policy.

<details>
<summary><b>System layers: what each part does</b></summary>

| System | Layer | What it does |
|---|---|---|
| **System 2** (reasoning) | **Agent** | Plain tool-calling loop on Qwen3.8-27B (FP8, vLLM): picks the workflow, handles shortages and blocked picks, reports outcomes |
| | **Perception** | Same model with image input: bin checks, grounding the described item, tote audit |
| **System 1** (skills) | **Executor** | Persistent task state machine: perception, grasp, push, recovery policies, splices new steps into running tasks |
| | **WMS** | FastAPI + Postgres: reservations, picks, cycle counts and audit rows in single transactions |
| **System 0** (motion) | **Isaac Sim** | Ridgeback + Franka, Lula IK, depth-based pose estimation, push primitive, conveyor, camera and MJPEG streams |
| | **Learned policies** | SmolVLA and diffusion policy servers (grasp and push), with the scripted planner as fallback |
| **Delivery** | **Dashboard** | React + Vite: agent chat with tool trace, live sim stream, 2D floor map, orders and exceptions |

</details>

<details>
<summary><b>Agent tools: the 17 typed tools the agent can call</b></summary>

| Group | Tool | What it does |
|---|---|---|
| **Orders** | `list_orders` | List orders, optionally by status |
| | `get_order` | Order with its lines (SKU, quantity, picked) |
| | `plan_fulfillment` | Reserve stock and create a pick task with an optimized route; fails with a reason when short |
| **Inventory** | `find_stock` | Shelf locations and available quantity for a SKU or product name |
| | `get_location` | Contents of a location |
| | `find_empty_bins` | Empty storage bins, optionally in one aisle |
| | `inspect_inventory` | Plan a VLM cycle count over bins or an aisle; corrects the records |
| | `relocate_stock` | Plan a move of stock between storage bins |
| | `replenish_stock` | Plan a move of received stock from staging to a bin |
| **Robot & tasks** | `get_robot_status` | Robot status, position, battery and tote contents |
| | `dispatch_task` | Send a planned task to the robot (asynchronous) |
| | `get_task_status` | Task status and per-step progress |
| | `wait_for_task` | Block until a task finishes, when the next step depends on it |
| | `clear_blocker` | Splice `take_tool paddle → push_blocker → return_tool` before a blocked pick |
| | `cancel_task` | Cancel a task and release its reservations |
| **Exceptions** | `list_exceptions` | List open, resolved or escalated exceptions |
| | `report_exception` | Record an exception that needs human attention |

</details>

<details>
<summary><b>Iterative build progression</b></summary>

| Stage | Core addition | Result |
|---|---|---|
| Agent + mock robot | Typed WMS tools, executor, fault injection | [Agent 75% vs 42% scripted under faults](docs/DESIGN.md#fulfillment-kpis-evalrun_evalpy) |
| Isaac Sim + VLM | Warehouse scene, bin inspection, tote audit | [96–100% presence, 6 / 6 audits](docs/DESIGN.md#shelf-bin-perception-evalvlm_evalpy) |
| YCB grasping | Rigid YCB items, SmolVLA and diffusion on 400 demos | [SmolVLA 67% vs scripted 57%](docs/DESIGN.md#grasping-scripted-vs-smolvla-vs-diffusion---eval-grasp) |
| Language grounding | VLM points at the described item | [72 / 72](docs/DESIGN.md#language-grounding-evalgrounding_evalpy) |
| **Deep shelving** | Side stance, paddle tool, push primitive, push VLA, teleop | [Action items](docs/ACTION_ITEMS.md) |

</details>

Design: [design notes and full results](docs/DESIGN.md), [action items](docs/ACTION_ITEMS.md).

---

## Quickstart

**Prerequisites:** Linux, an NVIDIA GPU (tested on an L40S 48GB), Docker with the NVIDIA runtime, and the Qwen3.8-27B-FP8 weights in the Hugging Face cache.

### 1. Configure

```bash
git clone https://github.com/Michae1Park/agentic-warehouse-automation.git && cd agentic-warehouse-automation
cp .env.example .env                        # ROBOT_BACKEND=mock for fast mode
```

### 2. Run the Isaac demo

```bash
scripts/demo.sh                             # high graphics, WebRTC, CPU-pinned, GPU budget check
```

GPU memory: Isaac ~7GB, VLA server ~1.3GB, Qwen3.8-27B at `LLM_GPU_UTIL=0.75` ~34.5GB. `demo.sh` refuses to start if other GPU jobs would push it over.

### 3. Or run without the simulator

```bash
docker compose up -d                        # postgres, vLLM, backend, dashboard
open http://localhost:5173
```

### 4. Evaluate

```bash
python3 eval/run_eval.py --seeds 1 2 3 --orders 8    # fulfillment KPIs, mock robot
python3 eval/vlm_eval.py                             # bin perception, needs the isaac profile
```

<details>
<summary><b>Once open: what you can do</b></summary>

- **Chat:** ask the agent to *"Prepare order #1045"*, *"Cycle count aisle B"* or *"Prepare order #1044"* (short on the shelf). Every turn shows the tool trace.
- **Isaac view:** drag to orbit, right/shift-drag to pan, scroll to zoom, double-click or **reset view** to return to the overview.
- **Teleop:** `scripts/teleop.sh` opens a browser controller on :8020 for push demos and VLA intervention.

</details>

Full steps, limitations and troubleshooting: [Design notes](docs/DESIGN.md#running-it).

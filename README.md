# Agentic Warehouse Automation

**Integrating LLMs, VLMs and robot simulation for autonomous order fulfillment.**

A warehouse manager types *"Prepare order #1042 and move it to the dispatch area."* An LLM agent reads the order
from the system of record (Postgres), reserves inventory, plans a pick route and dispatches a mobile manipulator in
Isaac Sim. The robot uses a VLM to check each shelf bin, then picks and carries the items to an outbound conveyor,
where a VLM audit checks the tote against the order before it ships.
Every physical step writes back to the database. The dashboard, chat and live simulation stay in sync because they
all read from that one system of record.

This is an engineering project, not a research one: no foundation models are trained. The work is integration of
existing models and tools into one reliable, measurable workflow.

![Dashboard](docs/images/dashboard.png)

**Demo video:** [docs/demo/demo.mp4](docs/demo/demo.mp4) (3× speed): fulfillment with an empty bin recovered, VLA picks, restock into an empty bin, and a refused non-pickable request.

---

## 1. What business problem does this solve?

Warehouse operations run on a warehouse management system (WMS). People turn WMS records into physical work
(pick lists, walking routes, cycle counts) and then turn the physical results back into records (picked
quantities, stock adjustments, exception tickets). Both translations are slow, and mismatches between records and
the shelves cause failed picks, oversells and manual investigations.

The demo covers six operational workflows end to end:

| Workflow | Example request | What happens |
|---|---|---|
| Order preparation | "Prepare order #1042 and move it to dispatch" | reserve → route → pick → drop into outbound tote → VLM tote audit → release to conveyor → close order |
| Package retrieval | (same flow, single line orders) | |
| Inventory inspection | "Cycle count aisle B" | robot counts bins with the VLM and corrects the WMS |
| Stock replenishment | "Replenish green tea from receiving" | staging → shelf, inventory moved transactionally |
| Item relocation | "Move 3 yoga blocks from C-03-1 to A-01-2" | |
| Exception handling | missing stock, failed grasp, blocked path, shortages | automatic recovery or escalation with a report |

## 2. Why does it require robotic automation?

Picking, carrying and counting are the labour-intensive parts of fulfillment, and they are the steps where WMS
records and physical reality drift apart. A robot that takes part in the business process, reading the order,
updating the record and raising the exception, closes that loop. A robot that only moves boxes does not.

## 3. Why these models and frameworks?

| Layer | Choice | Why |
|---|---|---|
| Reasoning + tool calling | **Qwen3.8-27B** (FP8, vLLM) | Open weights, strong native tool calling, and multimodal, so one ~29GB model serves as both agent LLM and VLM on a single L40S alongside Isaac Sim |
| Vision | Same Qwen3.8-27B, image input | Shelf-bin SKU identification and counting. Scored against simulator ground truth (see Results) |
| Simulation | **Isaac Sim 5.1** (container) | Physically simulated Ridgeback + Franka mobile manipulator, RTX rendering for the VLM camera. `--graphics high` builds the scene from Isaac's warehouse assets (pallet racking, cartons, roller conveyor) |
| Arm motion | Lula IK (Isaac motion generation) | Reliable scripted front grasps. Also serves as the baseline the VLA is compared against |
| Agent | Plain tool-calling loop, no framework | One agent with typed tools and persistent state. A deterministic executor does the physical work |
| System of record | **Postgres** + FastAPI | Transactions for reservations and picks. `LISTEN/NOTIFY` triggers stream every change to the UI |
| UI | React + Vite | Operations dashboard, agent chat with tool trace, Isaac MJPEG stream, live 2D floor map |

**Deliberately avoided:** multi-agent frameworks. One agent with structured tools, a persistent task state machine
and deterministic recovery policies proved more reliable than agents passing messages to each other.

## 4. How does the architecture work?

The design is an ordinary tool-calling AI agent. The difference is that some of its tools happen to be robots.
The agent calls `dispatch_task` the way a coding agent calls `run_tests`: it states intent, gets a structured
result back, and decides what to do next. Underneath, the work is split into three systems that run at different
speeds:

![Architecture](docs/images/architecture.svg)

| System | Role | Analogy to a software agent |
|---|---|---|
| **System 2** | Decides *what* to do: chooses the workflow, handles ambiguity and shortages, reports outcomes | The LLM in the agent loop |
| **System 1** | Turns a task into skills and runs them reliably: perception, grasping, recovery. Plans are rows in `tasks` / `task_steps`, so a restart resumes a running task | The tool's implementation |
| **System 0** | Turns skill targets into motion: IK, joint control, physics | The OS / hardware the tool runs on |

## 5. How does the agent interact with enterprise systems?

Through typed tools that wrap SQL transactions, never free-form SQL:

`list_orders`, `get_order`, `find_stock`, `get_location`, `find_empty_bins`, `get_robot_status`,
`plan_fulfillment`, `inspect_inventory`, `relocate_stock`, `replenish_stock`, `dispatch_task`, `wait_for_task`,
`get_task_status`, `cancel_task`, `list_exceptions`, `report_exception`

- **Reservations** happen at planning time, in one transaction with `SELECT … FOR UPDATE`, so two plans cannot
  promise the same unit.
- **Each pick** decrements `inventory`, releases the reservation, increments `order_lines.picked_qty`, adds the item to
  the robot's tote and appends an `inventory_transactions` audit row, all in one transaction.
- **Cycle counts** write `cycle_count` adjustments with the observed delta.
- **The outbound tote is audited** before it is released down the conveyor: the VLM compares the tote contents with
  the order and raises a `tote_mismatch` exception (pack-station QA) on any difference.
- **The order closes** only after a `verify` step confirms every line is fully picked and placed.

## 6. What happens when something fails?

Faults are injected into a **physical world model** that is separate from the database, so the WMS can actually be
wrong. That is the situation inventory accuracy measures.

| Fault | Recovery policy | Escalation |
|---|---|---|
| Grasp slip | Regrasp, up to 3 attempts | Task fails → exception → agent report |
| Path blocked | Replan, up to 3 attempts | same |
| Stock missing at bin | VLM sees the bin is empty → cycle-count the bin to 0 → reallocate from an alternate bin → splice new steps into the running task | No alternate stock → task fails, reservations released, agent explains and proposes options |
| Stock shortage at planning | Agent does not dispatch | Logs exception, checks receiving, offers replenishment |

When a task finishes, the executor sends a `[SYSTEM EVENT]` into the conversation that created it. The agent then
inspects the task and its exceptions and reports to the manager without being asked.

## 7. What can be measured?

See [Results](#results). All numbers come from simulation and are compared against **our own scripted simulation
baseline, not a real warehouse.**

---

## Results

All numbers are simulated and compared against our own scripted baseline, not a real warehouse.

### Fulfillment KPIs (`eval/run_eval.py`)

Seeds 1, 2, 3 · 8 generated orders per run · mock robot at 20× time scale · agent = Qwen3.8-27B.
Fault profile `faulty`: grasp failure 15%, blocked path 10%, missing stock 20%.

| Mode | Faults | Orders | Fulfillment | Interventions / order | Avg execution (sim) | Inventory accuracy | Recovery success | Agent turn latency |
|---|---|---|---|---|---|---|---|---|
| Scripted baseline | nominal | 24 | 92% | 0.08 | 30.0s | 100% | — | — |
| Scripted baseline | faulty | 24 | 42% | 0.58 | 24.0s | 77% | 0% (12) | — |
| Scripted + recovery | nominal | 24 | 92% | 0.08 | 29.4s | 100% | — | — |
| Scripted + recovery | faulty | 24 | 71% | 0.29 | 30.9s | 91% | 74% (19) | — |
| **Agent** | nominal | 24 | **100%** | **0.00** | 33.3s | 100% | — | 11.9s |
| **Agent** | faulty | 24 | **75%** | **0.25** | 32.4s | 94% | 71% (21) | 12.9s |

### Shelf-bin perception (`eval/vlm_eval.py`)

24 bins per seed, 25% of bins with a wrong WMS record, high-graphics scene, Qwen3.8-27B.

| Seed | Presence accuracy | Exact count | Wrong records detected | Latency |
|---|---|---|---|---|
| 7 | 100% | 75% | 8 / 8 | 1.2s |
| 11 | 96% | 67% | 4 / 4 | 1.3s |
| 23 | 96% | 75% | 6 / 6 | 1.3s |

Exact counts are limited by boxes hidden behind the front row, so cycle counts use presence (see Limitations).

### Grasping: scripted vs SmolVLA vs diffusion (`--eval-grasp`)

> Measured before the side-stance rework (base facing the shelf). The current baseline runs the scripted grasp; both learned policies need new demos from the side stance and are disabled until retrained.

YCB objects on realistic shelves (same-product clusters, some toppled background items). 30 randomized trials per
policy, attach-on-grasp, `--graphics basic` (`eval/results/grasp_ycb_smolvla/`, `eval/results/grasp_ycb_diffusion/`).
Both learned policies were trained on the same 400 successful scripted demonstrations.

| Policy | Overall | Mustard bottle | Tomato soup can | Mean time (sim) |
|---|---|---|---|---|
| Scripted (Lula IK, true pose) | 57% | 21% | 88% | 8.1s |
| **SmolVLA** (language-conditioned) | **67%** | 36% | 94% | 9.0s |
| Diffusion policy (vision only) | 63% | 29% | 94% | 8.0s |

The learned policies match or beat the scripted planner they were trained on, because they only saw its successful
episodes. The mustard bottle (tall, narrow, often on an upper shelf) is the open problem for all three policies.
In the agent, SmolVLA runs as the primary picker with the scripted grasp as fallback (`GRASP_POLICY=vla_with_fallback`).
Verified end to end: orders #1040, #1041 and #1042 fulfilled with every pick done by SmolVLA and tote audits passed.

### Language grounding (`eval/grounding_eval.py`)

Before each pick the VLM points at the described item in the bin image and the simulator grasps the object nearest
to that point. 3 seeds, three phrasings per stocked bin (name, full description, attribute like "the red can"):
**72 / 72** correct, 1.2s per query.

### Outbound tote audit

Isaac, high-graphics scene, Qwen3.8-27B. Six generated orders (1–4 units each): **6 / 6** audits correct with exact
counts, 1.6–3.1s per audit. Mis-packed totes (the order claims different SKUs than the tote holds): the VLM reports
the actual contents in **2 / 2** cases instead of echoing the order, so the mismatch is raised.

---

## Limitations (what is simplified)

This is a portfolio demo, so the simplifications are stated plainly:

- **Grasp physics.** YCB objects are rigid bodies with convex-hull colliders, but the demo uses attach-on-grasp: once
  the fingers close on the item it is attached to the hand. Physical grasping (`--grasp physical`) reaches 65% and
  stays experimental until it passes 80%.
- **Side-stance manipulation.** The base parks parallel to the shelf, receiving pallet or conveyor and slides to line the arm up with the item; the arm grasps with a horizontal gripper, backs straight out and sets the item down in a tote on a raised stand behind it (a horizontal gripper cannot reach the deck itself). Dispatch and stow are the reverse transfer. The scripted grasp plans from the simulator's object pose; the VLM decides which object.
- **Two pickable products.** Mustard bottle and tomato soup can are robot-pickable; the other 10 YCB products are
  real WMS SKUs flagged `robot_pickable = false`, which the agent refuses and routes to manual handling.
- **Navigation.** A corridor waypoint planner inside the simulator drives the holonomic base. ROS2 / Nav2 and MoveIt2
  are not integrated. The `RobotGateway` interface is where they would plug in.
- **VLM counting.** From a single front-facing camera the VLM reliably identifies products and detects empty or wrong
  bins, but it cannot count boxes hidden behind the front row. Cycle counts therefore use VLM presence (zero-out
  missing SKUs, flag unexpected ones) rather than VLM quantities.
- **Faults are injected**, with seeded probabilities, into a physical world model that is separate from the database.
- **The VLA** is SmolVLA fine-tuned (8k steps, 34 min) on 400 successful scripted demonstrations of the arm phase
  only, with a 6-D end-effector action space (tip xyz, yaw, pitch, gripper) and three instruction phrasings. Base
  approach, lifting and placing remain scripted. Data collection and evaluation use `--graphics basic`. The diffusion
  baseline uses one observation frame and no language.
- **Conveyor.** Totes are moved kinematically along the roller conveyor and removed at the outbound pallet; there is
  no packing or shipping workflow behind it. Items are released into the tote at the drop pose rather than dropped
  with physics.
- **Background racks** in the high-graphics scene are decoration and are not part of the WMS.

---

## Running it

Requirements: NVIDIA GPU (tested on an L40S 48GB), Docker with the NVIDIA runtime, and the Qwen3.8-27B-FP8 weights in
the Hugging Face cache.

One-command Isaac demo (high graphics, WebRTC, CPU-pinned, GPU budget check):

```bash
scripts/demo.sh                      # GRASP_POLICY=vla_with_fallback scripts/demo.sh to use the VLA
```

In the dashboard's Isaac view: drag to orbit, right/shift-drag to pan, scroll to zoom, double-click or
**reset view** to return to the overview.

GPU memory (L40S, 46GB): Isaac high graphics ~7GB, VLA server ~1.3GB, Qwen3.8-27B at `LLM_GPU_UTIL=0.75` ~34.5GB.
That fits, but with little headroom; `demo.sh` refuses to start if other GPU jobs would push it over. For
development, set a lighter model in `.env` (see `.env.example`).

```bash
cp .env.example .env                 # ROBOT_BACKEND=mock for fast mode
docker compose up -d                 # postgres, vLLM, backend, dashboard
open http://localhost:5173

# Isaac Sim mode
echo -e "ROBOT_BACKEND=isaac\nSIM_URL=http://localhost:8020" > .env
docker compose --profile isaac up -d

# Isaac Sim mode with the WebRTC Streaming Client (connect it to this machine's IP)
echo 'ISAAC_ARGS="--livestream --graphics high"' >> .env
docker compose --profile isaac up -d --force-recreate isaac
```

The livestream uses TCP 49100 (signaling) and UDP 47998 (media); open them if the client is on another machine.

Evaluation (mock robot, fast):

```bash
python3 eval/run_eval.py --seeds 1 2 3 --orders 8
python3 eval/vlm_eval.py            # needs the isaac profile running
```

## Repository layout

```
db/            schema (tables, triggers, views) and seed data
backend/app/   FastAPI: agent/, executor.py, warehouse.py, perception.py, robot/{mock,isaac}.py, sim/world.py
isaac/         Isaac Sim warehouse server (scene, conveyor, robot control, camera API, MJPEG stream)
vla/           SmolVLA dataset conversion, training and policy server
scripts/       demo launcher
frontend/      React dashboard
eval/          KPI harness and VLM perception benchmark
docs/          action items, images
```

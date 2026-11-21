# Agentic Warehouse Automation

**Integrating LLMs, VLMs and robot simulation for autonomous order fulfillment.**

A warehouse manager types *"Prepare order #1042 and move it to the dispatch area."* An LLM agent reads the order
from the system of record (Postgres), reserves inventory, plans a pick route and dispatches a mobile manipulator in
Isaac Sim. The robot uses a VLM to check each shelf bin, then picks, carries and places the items at dispatch.
Every physical step writes back to the database. The dashboard, chat and live simulation stay in sync because they
all read from that one system of record.

This is an engineering project, not a research one: no foundation models are trained. The work is integration of
existing models and tools into one reliable, measurable workflow.

![Dashboard](docs/images/dashboard.png)

---

## 1. What business problem does this solve?

Warehouse operations run on a warehouse management system (WMS). People turn WMS records into physical work
(pick lists, walking routes, cycle counts) and then turn the physical results back into records (picked
quantities, stock adjustments, exception tickets). Both translations are slow, and mismatches between records and
the shelves cause failed picks, oversells and manual investigations.

The demo covers six operational workflows end to end:

| Workflow | Example request | What happens |
|---|---|---|
| Order preparation | "Prepare order #1042 and move it to dispatch" | reserve → route → pick → dispatch → verify → close order |
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
| Reasoning + tool calling | **Qwen3.5-9B** (FP8, vLLM) | Open weights, strong native tool calling, and multimodal, so one ~14GB model serves as both agent LLM and VLM on a single L40S alongside Isaac Sim |
| Vision | Same Qwen3.5-9B, image input | Shelf-bin SKU identification and counting. Scored against simulator ground truth (see Results) |
| Simulation | **Isaac Sim 5.1** (container) | Physically simulated Ridgeback + Franka mobile manipulator, RTX rendering for the VLM camera |
| Arm motion | Lula IK (Isaac motion generation) | Reliable scripted front grasps. Also serves as the baseline the VLA is compared against |
| Agent | Plain tool-calling loop, no framework | One agent with typed tools and persistent state. A deterministic executor does the physical work |
| System of record | **Postgres** + FastAPI | Transactions for reservations and picks. `LISTEN/NOTIFY` triggers stream every change to the UI |
| UI | React + Vite | Operations dashboard, agent chat with tool trace, Isaac MJPEG stream, live 2D floor map |

**Deliberately avoided:** multi-agent frameworks. One agent with structured tools, a persistent task state machine
and deterministic recovery policies proved more reliable than agents passing messages to each other.

## 4. How does the architecture work?

```
 ┌──────────────── React dashboard ───────────────────┐
 │ KPIs · Orders · Inventory · Robot · Exceptions      │
 │ Agent chat (tool trace)   Isaac stream / floor map  │
 └───────▲──────────────── REST + WebSocket ───────────┘
         │
 ┌───────┴──────── FastAPI backend ─────────────────────────────────┐
 │  Agent loop ──tools──▶ Warehouse domain layer (SQL transactions)  │
 │      ▲                        │ creates tasks + steps             │
 │      │ [SYSTEM EVENT]         ▼                                   │
 │  Task executor (per robot queue, resumable state machine)         │
 │      │  navigate · inspect · pick · place · stow · count · verify │
 │      ├──▶ Perception (VLM on inspection camera)                   │
 │      └──▶ RobotGateway ─┬─ MockRobot (fast, for evals)            │
 │                         └─ IsaacRobot (HTTP → Isaac Sim)          │
 └──────────▲──────────────────────────────────────────────────┬────┘
            │ LISTEN/NOTIFY                                    │
       Postgres (system of record)                     Isaac Sim 5.1
                                                      Ridgeback-Franka
```

**Separation of concerns.**
- The **LLM decides what to do**. It reads orders and stock, chooses the workflow, handles ambiguity and shortages,
  and explains the outcome.
- The **executor does it, deterministically**. Plans are rows in `tasks` / `task_steps`. Each step's status, attempts
  and result are persisted, so a backend restart resumes a running task.
- The **gateway abstracts the robot**. The same executor drives the mock robot used for fast evaluation and the
  Isaac Sim robot.

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

_Filled in by `eval/run_eval.py` and `eval/vlm_eval.py`; see `eval/results/`._

---

## Running it

Requirements: NVIDIA GPU (tested on an L40S 48GB), Docker with the NVIDIA runtime, and the Qwen3.5-9B weights in
the Hugging Face cache.

```bash
cp .env.example .env                 # ROBOT_BACKEND=mock for fast mode
docker compose up -d                 # postgres, vLLM, backend, dashboard
open http://localhost:5173

# Isaac Sim mode
echo -e "ROBOT_BACKEND=isaac\nSIM_URL=http://localhost:8020" > .env
docker compose --profile isaac up -d
```

Evaluation (mock robot, fast):

```bash
python3 eval/run_eval.py --seeds 1 2 3 --orders 8
python3 eval/vlm_eval.py            # needs the isaac profile running
```

## Repository layout

```
db/            schema (tables, triggers, views) and seed data
backend/app/   FastAPI: agent/, executor.py, warehouse.py, perception.py, robot/{mock,isaac}.py, sim/world.py
isaac/         Isaac Sim warehouse server (scene, robot control, HTTP API, MJPEG stream)
frontend/      React dashboard
eval/          KPI harness and VLM perception benchmark
docs/          action items, images
```

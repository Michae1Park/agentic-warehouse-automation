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
- [x] Demo video `docs/demo/demo.mp4` (3× speed, 3 min): #1042 with an empty bin recounted and rerouted, all picks by SmolVLA, tote audit; restock of "red soup cans" into an empty bin; non-pickable cracker boxes refused (`manual_handling_required`)

## Step 9 — Reliability (Isaac soak test)

- [x] Soak harness `eval/soak_eval.py`: every scoped workflow through the agent chat in Isaac, 3 seeds (2 faulty). Run 1: 21 requests, 0 stuck, 0 sim crashes, 0 HTTP errors; 16 tasks succeeded, 6 failed (5 correct fault handling, 1 VLM label error); 2 requests hit the agent step limit; tote audit false alarms 3/10 (`eval/results/soak/`)
- [x] Fix: replacement totes could stay hidden when two dispatch spots released within 2s
- [x] Agent tools resolve product names (agent had hit its step limit on "Move 2 units of Wireless Mouse …"); verified: relocation queued on the first try
- [x] Label colors tuned for the high-graphics lighting (brown, orange) + prompt hint; verified brown/orange totes pass. Grey still reads as white (open, superseded by the YCB pivot)
- [ ] Known: grey labels read as white or as a non-existent SKU (low contrast under bright lighting); superseded by the YCB pivot
- [x] Cold-start `scripts/demo.sh` (LLM before Isaac, `/health` wait, 1m45s cached) and `GRAPHICS=basic` launch with #1042 fulfilled. Soak re-run skipped: superseded by the YCB pivot

## Step 10 — YCB pivot: language-conditioned grasping

Plan: [YCB Pivot Plan](https://claude.ai/artifact/DEaQ4NEy4PeF2sDDCsHHaj). Decisions: Isaac YCB objects, mixed bins with random poses, physical contact grasping, SmolVLA + diffusion policy vs scripted, descriptions and SKUs in chat, affordances learned implicitly.

- [x] M0 Stabilize baseline: soak fixes deployed, pre-pivot demo tagged `pre-ycb-pivot` (soak re-run skipped)
- [x] M1 (prototype scope) YCB catalog + messy bins: 2 pickable SKUs (mustard bottle, tomato soup can) with descriptions; 10 other YCB objects as non-WMS background clutter; random positions/yaw (catalog items in the front band); under-shelf strip lights; agent resolves descriptions ("red soup cans"). Verified e2e in Isaac: #1042 fulfilled + tote audit passed, relocation by description. Kinematic grasp for now
- [x] Realistic shelves: same-product clusters per bin (front row first, ±1 cm / ±7° jitter, 12% of non-pickable items toppled), never mixed piles; 10 other YCB objects are real WMS SKUs flagged `robot_pickable = false` (agent refuses robot tasks for them and logs `manual_handling_required`); 4 bins fully empty for restocking; stows extend the SKU's cluster; receiving pallet raised to 0.3 m with a front-row layout. Verified: restock into an empty bin, #1042 e2e. Shelf grasp (attach-on-grasp) 87% over 30 trials
- [ ] M1 follow-ups: expand pickable set (toppled pickables → Step 12, misplaced-item fault → Step 11)
- [ ] M2 Physical grasping (in progress): YCB items are rigid bodies with convex-hull colliders and real mass; finger drives capped (stiffness 1e7 → 2e3, max force 1200 → 40 N) so they no longer crush through items; yaw-aligned grasp from the true pose with base alignment, base back-off, Cartesian approach and 180° roll fallback. 20 held-out trials, seed 3: `--grasp physical` 65%, `--grasp kinematic` (attach on close) 70%. Demo default stays kinematic until physical is ≥ 80%; remaining failures: mustard drops during lift, IK limits at bin edges. 100+ trial run pending
- [x] M3 Language + grounding: agent resolves descriptions to SKUs; before each pick the VLM points at the described item in the bin image (0–1000 coords), the sim projects every object (catalog + clutter) into that view and grasps the nearest one, wrong objects raise `wrong_object` → regrasp. Benchmark `eval/grounding_eval.py`: 72/72 correct over 3 seeds (name / full description / "the red can"), 1.2s per query. Verified e2e: #1040 fulfilled with grounded picks
- [x] M4 Demonstrations: 400 successful episodes from the scripted 6-DoF grasp (arm phase only, layout reshuffled every 10 episodes, 3 instruction phrasings: name / description / "grab the {color} {noun}"), kept only if the lift holds (400 of 584 attempts, 68%); 230 soup can / 170 mustard (`vla/data/raw_ycb` → LeRobot)
- [x] M5 Trained on the same data with the LLM stopped (`vla/train.sh`, 8k steps, batch 32): SmolVLA 34 min, loss 0.072, 5.4 GB; diffusion baseline (n_obs_steps=1, not language-conditioned) 13 min, loss 0.011, 4.9 GB. Both load in `vla/policy_server.py`
- [x] M6 Evaluate + integrate: 30-trial YCB grasp eval: SmolVLA 67% (mustard 36%, soup 94%), diffusion 63% (29% / 94%), scripted 57% (21% / 88%) (`eval/results/grasp_ycb_{smolvla,diffusion}/`). Grounding 72/72 (wrong-object rate 0%). SmolVLA is the primary picker with scripted fallback; #1040, #1041, #1042 fulfilled with all picks by SmolVLA. Dashboard shows the grasp policy and VLA instruction per pick. Open: mustard grasps for all policies
- [x] M7 README (YCB grasp + grounding results, limitations), architecture diagram and action items updated

## Step 11 — Realistic manipulation

Baseline tagged `baseline-side-stance`: scripted grasp is the production path, learned policies are disabled until retrained.

- [x] Side stance: base parks parallel to the shelf / receiving pallet / conveyor (`approach_pose`, `face_x`, `STANCE_CLEARANCE`), arm faces work with `side_home` (joint1 = -90°). Base slides along the shelf to line the arm up with the item (`align_to`); arm handles reach and height only. Base sidesteps away from the work face before turning and docks sideways on arrival (`UNDOCK`), so its corners never sweep the shelf
- [x] Real transfers instead of teleports: pick → lift 3 cm → retract straight out → set down in a deck-tote slot (`stash`); dispatch takes each item from the deck tote and sets it in the outbound tote (`take_from_deck`, `set_down`); stow carries from the deck tote into the shelf. Deck tote raised on a stand behind the arm (surface 0.72 m; a horizontal gripper cannot reach below ~0.8 m near the base, found with the new `/probe` IK command). Verified: 3 picks → dispatch place, receiving pick → stow into B-01-1, nothing knocked off
- [x] Grasp candidate search (pitch 0/0.2/0.4/0.6 × grasp height × roll) with a clean `ik_failed` instead of driving into the item; eval restores the whole bin after each trial
- [x] Spacing: pickable items in a single front row with `PICK_GAP` = 6 cm finger clearance (layout_bin + cluster_spot)
- [ ] Grasp eval on the side stance, 50 trials kinematic + 50 physical (`eval/results/grasp_side_{kin,phys}`), then e2e #1042 through the agent
- [ ] Re-collect demos and retrain SmolVLA + diffusion on the new stance (old checkpoints assume the front stance; demo runs `GRASP_POLICY=scripted` until then)
- [x] Misplaced-stock fault: `misplaced_stock_rate` in `FaultConfig` moves a pickable SKU's stock to another bin in the physical world; inspection records unrecorded pickable stock by cycle count (`record_found_stock`) and raises a resolved `inventory_discrepancy`. Not yet exercised in an e2e run
- [x] CI: GitHub Actions lint, backend smoke test (Postgres + mock robot: #1042 fulfilled, #1044 refused), frontend build
- [x] Decide the VLA's role: harder scenarios that need it (Step 12)
- [ ] Then: expand pickable set (candidates: sugar box, potted meat can), mustard grasp rate, physical grasp ≥ 80% (toppled pickables moved to Step 12)
- Diagnosis behind this step: with the base facing the shelf, `link0` (0.30 m ahead of base centre) ends up only ~0.30 m from front-row items, forcing a folded wrist; after the lift the arm swung to home beside the shelf and the item was teleported to the deck. Mustard failures (eval 30 trials): upper shelf C-03-2 out of reach, IK failures at 0.43 m, and pregrasp misses that knocked items up to 16 cm


## Step 12 — Scenarios that need a VLA

Decision: the VLA must earn its place. Three hard scenes, a fair perception-only baseline, and a privileged sim-only expert for demos. No ablations; the reasoning goes in the README.

- [x] Fair baseline: deployed scripted planner uses a pose estimated from the depth camera (segment + fit), never the simulator pose (`--pose depth`). Easy scene, 20 trials physical: depth 75% vs simulator-pose expert 55% (`eval/results/grasp_depth_phys/`). Target mask still comes from simulator instance ids
- [ ] Privileged expert: demo generator uses simulator poses (only possible in sim), with perturbations (mid-approach nudge, grasp offset, partial slip) and recorded corrections
- [x] Grasp success: kinematic attach, but only when the fingertip is inside the item's box and the closing width fits (physical grasping dropped as a requirement)
- [x] Scene 1 — Toppled shelf items (B-01-1, 25% elsewhere): soup cans lying at random yaw. Mustard stays upright: lying flat it is 9.6 cm wide (> 8 cm gripper) and upside down it falls over
- [x] Scene 2 — Cluttered receiving tote (STAGING-1): 0.74 × 0.50 m tote, 8 pickables + 8 clutter items dropped in layers with random tilt, top-down grasps. Background pallets with filled and stacked totes for realism
- [x] Shelf realism: items at varied yaw (pickables ±57°, others ±29°; mustard capped because side-on it is 9.6 cm, wider than the gripper)
- [x] Scene 3 — Mixed-SKU deep shelving (C-04-1): mustard behind a cracker box, soup can behind a sugar box; expert pushes the blocker sideways (`push_aside`), scripted baseline has no push. Smoke test: expert pushes and lifts the mustard, scripted fails
- [x] Full eval per scene, scripted vs expert (`eval/results/grasp_hard_v4/`, 30 trials): toppled 82% vs 64%, tote 58% vs 58% (all scripted tote failures are `no reachable grasp`, a reach limit, not perception), deep 0% vs 14%
- [x] Decision: the depth-based scripted planner handles toppled and tote, so only deep shelving needs the VLA. Toppled and tote stay as generalisation scenes; the expert's push-then-grasp (1/7) is the bottleneck for demos
- [ ] Demo collection upgrades (`collect_demos`), scoped to what makes the demo:
  - [ ] Push primitive and grasp recorded as one episode (deep shelving)
  - [ ] Perturbations with recorded recovery: mid-approach object nudge, grasp offset, partial slip
  - [ ] VLM grounding point drawn as a marker on the head image (unambiguous target in clutter)
  - [ ] Wrist camera as a second image stream (occlusion in deep shelving, close-in tote grasps)
  - [ ] Plumbing: episodes start from the home pose, not the scripted pregrasp, so the VLA replaces pose estimation; 7-D action with tool roll
  - Dropped: scene-aware instructions (marker covers it)
- [ ] Collect demos per scene, retrain SmolVLA + diffusion (head camera now faces the work side)
- [ ] Eval per scene: depth-based scripted vs SmolVLA vs diffusion
- [ ] README: why each scene breaks the scripted planner; FIFO/expiry rotation as a further real-world reason for back-row picks; deformables (bags, pouches) as future work


## Step 13 — Three portfolio demos

Goal: three realistic warehouse tasks, each showing one applied AI technology.

1. **Order fulfillment with a deep-shelving pick (VLA)**: the agent plans and fulfills an order; the target sits behind a blocker, so a scripted step picks up the paddle, the VLA pushes the blocker aside, and the fetch is a separate scripted action
   - [x] Scope: the VLA is trained only for the push; paddle pickup/return and the fetch are scripted actions, and the VLM orchestrates pickup → push → return → fetch
   - [x] Pickup stays scripted: the toolbox is mounted on the robot, so the motion never varies. Push episodes all start from the same ready pose (fingertip std ≤ 0.004 m over 15 episodes), so the handoff to the VLA is seamless
   - [x] Expert push lands on the blocker (closed-loop, exact retrace): in `push_v1`, 22/22 blockers upright, target never disturbed
   - [x] Faster push: sweep 0.03 / 0.05 / 0.08 m/s (seed 11, `eval/results/push_speed/`) gave identical outcomes (15/22 saved, 4 short, 3 skipped) with episodes 246 → 142 frames; 0.08 m/s is now the default stroke for all blockers (rotated-box slow speed removed)
   - [x] Push across object types: boxes slipped 1–3 cm short; up to 4 cm extra stroke (capped by free room), still stops at the goal. Seed 11: 15/17 saved (88%) vs 15/22 (68%) (`eval/results/push_boxfix/`)
   - [x] Push to the other side when the preferred side has no room: seed 11 gave 15/18 saved, 0 no-room skips (was 3), pushes go both ways, target never moved (`eval/results/push_sidefix/`). Remaining rejections are borderline: 1–3 mm short or 3.3 cm depth drift
   - [x] Assert the scripted pickup ends at the push ready pose (0.02 rad joint tolerance); no failures in 18 attempts
   - [x] Orientation encoding: Euler yaw/roll are singular at the ready pose (paddle straight down), and a rotation vector flips sign because ready → push is a ~180° turn the arm makes either way. Push demos now record a 6-D rotation (first two columns of the base-frame rotation): state/action are 10-D (xyz, 6-D rotation, gripper), consistent across episodes, max per-frame step 0.33. `convert_dataset.py --orientation rot6d` verified; `push_orientation` decodes actions back to a quaternion
   - Recorded episode = ready pose → approach → push → retrace → ready pose (142 frames at 0.08 m/s); head image carries the magenta target marker, wrist camera sees the paddle blade and the blocker
   - [ ] Push across blocker orientations (random yaw): rotated boxes pivot instead of sliding the full distance
   - [x] Collect `push_v1`: 200 episodes from 261 attempts (77%), 4.2 GB, seed 21, 0.08 m/s, 6-D rotation. Saved per blocker: master chef can 43/44, potted meat 42/52, foam brick 42/49, sugar box 33/43, gelatin 28/44, pudding 12/29. Rejected: 30 depth drift > 3 cm, 19 short, 11 no room, 1 toppled
   - [ ] Pudding box is under-represented (12/200 episodes, 41% yield) and depth drift is now the top rejection; look at both if the policy struggles on boxes
   - [ ] Train SmolVLA + diffusion on push demos (`TASK=push vla/train.sh`), eval push success per object type and orientation. SmolVLA trained: 200 episodes converted in ~95 min, 8k steps in ~50 min (2.3–3.7 steps/s, 8.6–9.5 GB VRAM), final loss 0.012 (`vla/outputs/smolvla_push/`)
   - [ ] Faster dataset conversion: 8 parallel image writers only took it from 1.2 to 2.1 episodes/min; the main process is CPU-bound (add_frame + per-episode stats). Convert episodes in parallel shards, or store videos instead of PNGs
   - [x] VLA push executor: the server is dimension-agnostic, so decoding lives in the sim (`push_vla`, 10-D actions via `push_orientation`). Scripted pickup + base alignment, then the VLA runs until the blocker passes the goal and the paddle is back near the ready pose (≤45 queries). `--collect-push N --push-policy vla` / `scripts/push_eval.sh` (30 trials, held-out seed 99, same success criteria as the expert)
   - Lesson: background waiters died with the session shell (training never auto-started overnight); don't rely on queued jobs surviving, check on resume
   - **Paused (2026-11-25): push VLA work on hold; the demo uses the scripted expert push (`PUSH_POLICY=expert`, default in `demo.sh`, compose and backend config). Resume from here:**
     - State: SmolVLA v1 (images + pose) 2/30 and v2 (+ marker state) 1/30 on held-out seed 99; execution bugs fixed (IK frame, chunk blending, continuous execution); remaining failures are left-push corner cutting, hovering after the stroke, and no recovery behaviour (scripted demos are all clean successes)
     - Teleop/intervention tool works but is not reliable enough to collect demos yet (latency, sim at ~0.36× real time, contact issues near the deck)
     - Next options: trim the 200 scripted demos to start at the push-ready pose (removes the left-push wrist detour, no re-collection); scripted recovery demos (perturb the blocker mid-push, expert re-plans; DART noise); record commanded targets at 30 Hz; or make teleop reliable and collect ~10–15 interventions
   - [ ] Push VLA closed loop: first held-out trials pushed 0 cm. Fixed two execution bugs: (1) rotations were decoded in the `panda_hand` frame but IK solves for `right_gripper` (fixed `gripper_frame` offset), so the wrist twisted from the first action; (2) arm lag, now each action settles to ≤ 5 mm (≤ 12 ticks). On the training scene (seed 21, ep 0) the policy now follows the expert path and pushes 0.107 m of 0.201 m, then returns home. Remaining: the paddle enters ~7 cm too close laterally (y −0.13 vs −0.20), matching a 3–5 cm lateral undershoot already visible offline → policy accuracy, not execution
   - [ ] Push VLA localization (option A, chosen): push VLA v1 (no marker) on held-out seed 99: 2/30 (7%, `eval/results/push_vla_v1/`; paddle lands on or too close to the blocker; the policy learned the motion but not the blocker's lateral position, which is ~10 px in the 256 px head image). Fix: append the grounded marker's head-image center (u, v) to the state (12-D). Marker found in 603/603 sampled frames. `vla/add_marker_state.py` patches the converted dataset in minutes instead of a 95 min re-conversion; sim adds the same feature with `--marker-state`. v2 trained: 8k steps, loss 0.011 (`vla/outputs/smolvla_push_marker/`); held-out eval and an expert eval on the same 30 scenes queued
   - [ ] Push VLA v2 (marker state) on seed 99: 1/30 (`eval/results/push_vla_v2/`; 15 toppled, 13 short). Not a localization problem: expert entry is y = −0.19 (left) / +0.19 (right), ±0.008 over 200 demos. Right pushes enter at +0.16 and nearly reach the goal; left pushes enter at −0.03…−0.10 and land on the blocker. For left pushes the expert detours (y +0.1 then −0.2 while the wrist turns ~180°) and the policy cuts the corner (5 cm undershoot offline too). Testing re-query every action (`--push-execute 1`) before changing the expert path (rotate at the ready pose, then straight to the push start) and re-collecting
   - [ ] Push VLA execution smoothness: on the live stream the policy follows the expert's path but moves in jolts and knocks blockers over. Causes found: (1) per-action settle wait made motion stop-and-go → removed (`--push-settle 0`), commanding one action ahead instead (`--push-lead 1`); (2) chunk-boundary jumps: speed change per frame 7.1 mm at chunk boundaries vs 2.7 mm for the expert → new chunks blended with the previous chunk's remainder over 5 steps (`--push-blend 5`). Re-querying every action (`--push-execute 1`) was dropped: ~10 min per trial. If still jerky: record commanded targets as actions at 30 Hz and simplify the expert's left-push path, then re-collect
   - [ ] Push VLA has no recovery behaviour: on the stream it follows the average expert path but doesn't react to a missed push or a rotating blocker (no better than a fixed-position controller), and after the stroke it hovers instead of retracting. Root cause: all 200 scripted demos are clean successes (entry ±0.8 cm), so there is nothing to learn about correction. Blending test (`push_vla_v2_blend/`, 7 trials): 0/7; right pushes 0.15–0.20 m, left pushes 0–0.09 m
   - [x] Teleop tool (`scripts/teleop.sh` → browser on :8020): overview stream plus the policy's head/wrist views, keyboard paddle control in the base frame (W/S, A/D, R/F, Q/E yaw, Shift fine), Enter save, Backspace discards and snaps the arm and items back to the same ready scene, N loads a new scene, Space pause; views: wrist (main by default), top-down over the bin, side across the shelf, overview, head (keys 1–5), paddle floor at push height (the hand pressing on the deck edge dragged the base 26 cm forward), IK solutions that jump > 0.15 rad rejected and joints rate-limited; polled as snapshots (six MJPEG streams hit the browser limit of 6 connections per host and froze the controls). Intervention mode: T turns on VLA assist, any motion key takes over, V hands back. Episodes start from the expert push-ready pose (paddle horizontal, raised beside the blocker on the push side), record the commanded target as the action (not the measured pose) and a per-frame human mask; idle frames are not recorded. `convert_dataset.py` takes several raw dirs, `--human-only` keeps teleoperated segments, `--human-repeat` oversamples them
   - [ ] Collect human recovery demos with intervention mode (focus: left pushes, boxes, rotated blockers), retrain on scripted + human data
   - [x] App wiring for demo 1: a pick at a storage bin with an item in front raises `blocked` (sim check before grasping); the executor pauses the task (status back to `planned`, step pending, fault + warning exception) and notifies the agent. The agent calls `clear_blocker(task_id)`, which inserts `take_tool paddle → push_blocker (VLA) → return_tool` before the paused pick, then `dispatch_task` resumes it. Verified the DB path; sim commands `take_tool`, `return_tool`, `push_blocker` exposed over HTTP
   - [x] Demo launcher: `scripts/demo.sh` runs the hard scenario and serves the push checkpoint (`PUSH_POLICY=vla`); order 1045 (5× tomato soup) takes 4 from A-02-1 and the last one from behind the coffee can at C-04-1
   - [ ] End-to-end demo run (UI → agent → park → clear_blocker → VLA push → pick → dispatch) once the push eval is done
   - [x] Demo 1 rescoped to plain fulfillment (no push, paddle and bracket removed from the scene); recorded end to end: order 1045, 5 cans from A-02-1 and C-01-1, unloaded, audited, shipped (`docs/img/demo-1-fulfillment.gif`, `docs/videos/demo-1-fulfillment.mp4`)
   - [x] Demo 1 re-recorded at 1.5× UI zoom with the original walkthrough and seed 0; agent replies limited to 3 short bullets (`docs/videos/demo-1-highlight.mp4`, 6.0 MB). Demos 2 and 3 still show the old, longer replies
   - [x] Demo 2 base motion: between bins of the same aisle the base now strafes along the scan line instead of returning to the corridor; scan poses are no longer approached with an undock/redock back-and-forth
   - [x] Stow into a shelf no longer sweeps the arm through the rack: the item is taken from the robot tote while backed off, the arm is set to the insertion pose outside the rack, and the base drives the item straight in and back out (hand at constant height and y). A toppled item is stowed still toppled
   - [x] Relocation fixes: stow takes the item from the robot tote with a side grip (reverse of a shelf pick, palm stays in front of the neighbours), picks a front-row spot with 10–14 cm clearance by scanning the whole bin and never falls back to a random spot (that fallback put the second soup against a mustard and knocked it over); the pre-grasp move is slowed in proportion to the largest joint turn instead of the two-step retry through work home
   - [x] Motion fixes found while recording: grasp choice now plans the approach and keeps the first smooth one (wrist `ik_jump` at A-02-1 predated the session); straight-line reaches plan the whole line and fall back to one joint move on a branch jump; arm settles on arrival instead of a fixed wait; held items keep their rotation in the hand; attachments update after the physics step (tote lagged a frame)
   - [x] Robot tote: walled KLT bin beside the arm (behind the arm needed joint 1 near its limit), items are physical inside it, collisions with the robot's own links filtered (the kinematic tray pushed the base and made it drift)
   - [x] Rack picks: rise ≤ 5 cm under the upper shelf, base backs out 35 cm before stashing; scan from the standoff pose and drive in once (no back-and-forth); base routes along aisle center lines (base is position-driven, so shelves cannot stop it)
   - [x] Dispatch: conveyor lowered to 80 %, outbound tote half depth, 90° curve (A02) to an opening in the east wall, drive bands hidden; per item the base backs off, the arm lifts, the base sidesteps to line up a free spot, carries above the rails (1.08 m, falls back to 1.03 / 0.98) and lowers straight down; `stage_tote` sim command tests dispatch alone in ~2 min
   - [x] Base speed 1.2 m/s with 0.6 m/s² ramps, arm 2.5×; bins capped to what fits on the shelf (27 cups at A-04-2 overflowed past the rack end and fell)
   - [ ] Separate fetch action after the push: post-push grasp currently fails (fingertip 0.6–0.7 m off the grasp-ready pose, `eval/results/push_stream/`)
2. **Cycle count with discrepancies (VLM)**: the agent counts bins by camera, finds short and misplaced stock, corrects the record and relocates the stock
   - [x] E2E run with `misplaced_stock_rate` and missing stock: recorded (seed 1, 1.5× UI zoom): 4 short bins corrected, 2 soup cans found misplaced at B-02-1, agent relocates them to B-01-1 (`docs/videos/demo-2-highlight.mp4`)
   - [x] Fixes found while recording: VLM count now records misplaced stock it sees (before, only an `unexpected_item` flag, so relocation had nothing to move); task-finished events carry the findings; prompt requires relocating misplaced stock home without asking (one take decided "no relocation needed")
   - [x] Misplacement fault restricted to level-1 bins for now (see open item below); VLM checked per seed before recording (seed 45 missed a mustard among boxes, misread SKU-2003 as SKU-2001)
   - [ ] Upper-level picks of tall items: head camera now rises/aims for level 2 (it missed the front of the shelf), but no grasp is reachable for a 19 cm mustard at the front of level 2 from the work stance; needs a different stance or approach (review together)
3. **Shortage handled from receiving (agent)**: an order is short on the shelf; the agent finds the stock in the cluttered receiving tote, replenishes, waits, then fulfills and the tote is verified at dispatch
   - [ ] Tote pick inside a replenishment task (grounded by the VLM, top-down grasp)
   - [x] E2E run: shortage → replenish → wait_for_task → fulfill → verify_tote, recorded with reset `scenario: "shortage"` (order 1044 = 3 mustards, 1 on the shelf at A-01-1, 4 in receiving); the seeded 12-mustard order was not actually short (shelves hold 12) and needed 12 picks
   - [x] Prompt recovers shortages covered by receiving without asking; tall items stay pinned in the robot tote (a mustard tipped over the 8 cm wall); base repositions up to 0.3 m and retries when no grasp is reachable (far side of the receiving tote)

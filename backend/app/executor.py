import asyncio
import logging
from collections.abc import Awaitable, Callable

from psycopg.types.json import Jsonb

from . import perception, warehouse
from .config import settings
from .db import fetch_all, fetch_one, pool
from .robot.gateway import RobotError, RobotGateway

log = logging.getLogger(__name__)

POLL_S = 0.5

TaskCallback = Callable[[dict], Awaitable[None]]


class StepFailed(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


async def record_fault(task_id: int, step_seq: int, code: str, detail: str, recovery: str | None) -> None:
    await fetch_one(
        "INSERT INTO task_faults (task_id, step_seq, code, detail, recovered, recovery) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        task_id, step_seq, code, detail, recovery is not None, recovery,
    )


class TaskExecutor:
    def __init__(self, robot: RobotGateway, on_task_finished: TaskCallback | None = None):
        self.robot = robot
        self.on_task_finished = on_task_finished
        self._callbacks: set[asyncio.Task] = set()

    async def run_forever(self) -> None:
        while True:
            try:
                task = await self._claim_next_task()
                if task:
                    await self._run_task(task)
                else:
                    await asyncio.sleep(POLL_S)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("executor loop error")
                await asyncio.sleep(POLL_S)

    async def _claim_next_task(self) -> dict | None:
        return await fetch_one(
            """
            UPDATE tasks SET status = 'running', started_at = COALESCE(started_at, now())
            WHERE id = (
                SELECT id FROM tasks
                WHERE robot_id = %s AND status IN ('running', 'queued')
                ORDER BY (status = 'running') DESC, id
                LIMIT 1 FOR UPDATE SKIP LOCKED
            )
            RETURNING *
            """,
            self.robot.robot_id,
        )

    async def _next_step(self, task_id: int) -> dict | None:
        return await fetch_one(
            "SELECT * FROM task_steps WHERE task_id = %s AND status IN ('pending', 'running') ORDER BY seq LIMIT 1",
            task_id,
        )

    async def _run_task(self, task: dict) -> None:
        await fetch_one(
            "UPDATE robots SET status = 'busy', current_task_id = %s WHERE id = %s RETURNING id",
            task["id"], self.robot.robot_id,
        )
        if task["order_id"]:
            await fetch_one(
                "UPDATE orders SET status = 'in_progress', started_at = COALESCE(started_at, now()) "
                "WHERE id = %s RETURNING id",
                task["order_id"],
            )

        try:
            while step := await self._next_step(task["id"]):
                try:
                    await self._run_step(task, step)
                except StepFailed as failure:
                    if not await self._recover(task, step, failure):
                        raise
        except StepFailed as failure:
            await self._fail_task(task, failure)
        else:
            await self._complete_task(task)
        finally:
            await fetch_one(
                "UPDATE robots SET status = 'idle', current_task_id = NULL WHERE id = %s RETURNING id",
                self.robot.robot_id,
            )

        if self.on_task_finished:
            finished = await fetch_one("SELECT * FROM tasks WHERE id = %s", task["id"])
            callback = asyncio.create_task(self.on_task_finished(finished))
            self._callbacks.add(callback)
            callback.add_done_callback(self._callback_done)

    def _callback_done(self, callback: asyncio.Task) -> None:
        self._callbacks.discard(callback)
        if not callback.cancelled() and callback.exception():
            log.error("task finished callback failed", exc_info=callback.exception())

    async def _run_step(self, task: dict, step: dict) -> None:
        await fetch_one(
            "UPDATE task_steps SET status = 'running', started_at = now(), attempts = attempts + 1 "
            "WHERE id = %s RETURNING id",
            step["id"],
        )
        try:
            try:
                result = await getattr(self, f"_do_{step['action']}")(task, step)
            except RobotError as err:
                raise StepFailed(err.code, str(err)) from err
        except StepFailed as failure:
            await fetch_one(
                "UPDATE task_steps SET status = 'failed', finished_at = now(), result = %s WHERE id = %s RETURNING id",
                Jsonb({"error": failure.code, "message": str(failure)}), step["id"],
            )
            raise
        await fetch_one(
            "UPDATE task_steps SET status = 'succeeded', finished_at = now(), result = %s WHERE id = %s RETURNING id",
            Jsonb(result or {}), step["id"],
        )

    async def _recover(self, task: dict, step: dict, failure: StepFailed) -> bool:
        if not settings.recovery_enabled or failure.code != "item_not_found" or not task["order_id"]:
            await record_fault(task["id"], step["seq"], failure.code, str(failure), None)
            return False
        rerouted = await warehouse.reroute_missing_stock(task["id"], step["id"])
        if not rerouted:
            await record_fault(task["id"], step["seq"], failure.code, str(failure), None)
            return False
        await record_fault(
            task["id"], step["seq"], failure.code, str(failure),
            f"cycle-counted {step['args']['location_id']} and rerouted to {', '.join(rerouted)}",
        )
        await warehouse.raise_exception(
            "inventory_discrepancy",
            f"{step['args']['sku']} recorded at {step['args']['location_id']} but not found; "
            f"inventory corrected and pick rerouted to {', '.join(rerouted)}",
            severity="info", task_id=task["id"], order_id=task["order_id"], status="resolved",
        )
        return True

    async def _location(self, location_id: str) -> dict:
        location = await fetch_one("SELECT * FROM locations WHERE id = %s", location_id)
        if not location:
            raise StepFailed("unknown_location", f"location {location_id} does not exist")
        return location

    async def _do_navigate(self, task: dict, step: dict) -> dict:
        location = await self._location(step["args"]["location_id"])
        navigate_to = getattr(self.robot, "navigate_to", None)
        max_attempts = settings.nav_max_attempts if settings.recovery_enabled else 1
        for attempt in range(1, max_attempts + 1):
            try:
                if navigate_to:
                    pose = await navigate_to(location["id"])
                else:
                    pose = await self.robot.navigate(float(location["x"]), float(location["y"]))
                return {"x": pose.x, "y": pose.y, "attempts": attempt}
            except RobotError as err:
                if attempt == max_attempts:
                    raise StepFailed(err.code, str(err)) from err
                await record_fault(task["id"], step["seq"], err.code, str(err), f"replanned path (attempt {attempt + 1})")
        raise AssertionError("unreachable")

    async def _perceive(self, location_id: str) -> tuple[dict[str, int], dict]:
        observation = await self.robot.inspect(location_id)
        detail = {"ground_truth": observation.counts}
        if settings.perception_mode != "vlm" or not observation.image_jpeg:
            return observation.counts, detail
        vision = await perception.identify_products(observation.image_jpeg, location_id)
        detail.update(vlm=vision["counts"], vlm_latency_ms=vision["latency_ms"],
                      vlm_presence_correct=set(vision["counts"]) == set(observation.counts))
        return vision["counts"], detail

    async def _do_inspect(self, task: dict, step: dict) -> dict:
        args = step["args"]
        counts, detail = await self._perceive(args["location_id"])
        found = await self._record_unexpected(task, args["location_id"], counts)
        if args["sku"] not in counts:
            raise StepFailed(
                "item_not_found",
                f"expected {args['sku']} at {args['location_id']}, observed {list(counts) or 'nothing'}",
            )
        return {"observed": list(counts), **({"found": found} if found else {}), **detail}

    async def _record_unexpected(self, task: dict, location_id: str, counts: dict[str, int]) -> list[dict]:
        recorded = {r["sku"] for r in await fetch_all(
            "SELECT sku FROM inventory WHERE location_id = %s AND quantity > 0", location_id)}
        pickable = {r["sku"] for r in await fetch_all("SELECT sku FROM products WHERE robot_pickable")}
        unexpected = {sku: max(qty, 1) for sku, qty in counts.items() if sku not in recorded and sku in pickable}
        if not unexpected:
            return []
        found = await warehouse.record_found_stock(task["id"], location_id, unexpected)
        for row in found:
            await warehouse.raise_exception(
                "inventory_discrepancy",
                f"{row['sku']} found at {location_id} but not recorded there (misplaced stock); "
                f"added {row['counted']} to inventory by cycle count",
                severity="info", task_id=task["id"], order_id=task["order_id"], status="resolved",
            )
        return found

    async def _do_pick(self, task: dict, step: dict) -> dict:
        args = step["args"]
        picked = args.get("picked", 0)
        max_attempts = settings.pick_max_attempts if settings.recovery_enabled else 1
        failed_attempts = 0
        policies: list[str] = []
        groundings: list[dict] = []
        instructions: list[str] = []
        while picked < args["quantity"]:
            try:
                point = None
                if settings.vlm_grounding and hasattr(self.robot, "resolve"):
                    grounding = await self._ground(args["sku"], args["location_id"])
                    groundings.append(grounding)
                    point = grounding["point"]
                    if point is None:
                        raise RobotError("not_grounded", f"VLM could not find {args['sku']} at {args['location_id']}")
                outcome = await self.robot.pick(args["sku"], args["location_id"], point) or {}
            except RobotError as err:
                failed_attempts += 1
                if err.code == "item_not_found" or failed_attempts >= max_attempts:
                    raise StepFailed(err.code, str(err)) from err
                await record_fault(task["id"], step["seq"], err.code, str(err), f"regrasp (attempt {failed_attempts + 1})")
                continue
            if outcome.get("policy_used") == "scripted_fallback":
                await record_fault(task["id"], step["seq"], "vla_grasp_missed",
                                   f"VLA grasp of {args['sku']} missed", "fell back to scripted grasp")
            policies.append(outcome.get("policy_used", "scripted"))
            if outcome.get("instruction"):
                instructions.append(outcome["instruction"])
            await warehouse.record_pick(task["id"], step["id"], self.robot.robot_id, args["inventory_id"], task["order_id"])
            picked += 1
        return {"picked": picked, "failed_attempts": failed_attempts, "grasp_policies": policies, "groundings": groundings,
                "instructions": instructions}

    async def _ground(self, sku: str, location_id: str) -> dict:
        product = await fetch_one("SELECT name, description FROM products WHERE sku = %s", sku)
        observation = await self.robot.inspect(location_id)
        result = await perception.ground(observation.image_jpeg, f"{product['name']} ({product['description']})")
        return {"point": result["point"], "latency_ms": result["latency_ms"]}

    async def _do_place(self, task: dict, step: dict) -> dict:
        location_id = step["args"]["location_id"]
        try:
            await self.robot.place(location_id)
        except RobotError as err:
            raise StepFailed(err.code, str(err)) from err
        placed = await warehouse.record_place(self.robot.robot_id)
        audit = await self._audit_tote(task, location_id, placed)
        try:
            await self.robot.release(location_id)
        except RobotError as err:
            raise StepFailed(err.code, str(err)) from err
        return {"placed": placed, **audit}

    async def _audit_tote(self, task: dict, location_id: str, placed: list[str]) -> dict:
        if settings.perception_mode != "vlm" or not placed:
            return {}
        observation = await self.robot.inspect(location_id)
        if not observation.image_jpeg:
            return {}
        expected: dict[str, int] = {}
        for sku in placed:
            expected[sku] = expected.get(sku, 0) + 1
        vision = await perception.verify_tote(observation.image_jpeg, expected)
        missing = sorted(set(expected) - set(vision["counts"]))
        unexpected = sorted(set(vision["counts"]) - set(expected))
        if missing or unexpected:
            await warehouse.raise_exception(
                "tote_mismatch",
                f"outbound tote at {location_id}: missing {missing or 'none'}, unexpected {unexpected or 'none'}; "
                "diverted to pack-station QA",
                task_id=task["id"], order_id=task.get("order_id"),
            )
        return {"tote_audit": {"expected": expected, "vlm": vision["counts"], "passed": not (missing or unexpected),
                               "vlm_latency_ms": vision["latency_ms"]}}

    async def _do_count(self, task: dict, step: dict) -> dict:
        location_id = step["args"]["location_id"]
        counts, detail = await self._perceive(location_id)
        if "vlm" in detail:
            counts, unexpected = await warehouse.presence_counts(location_id, counts)
            for sku in unexpected:
                await warehouse.raise_exception(
                    "unexpected_item", f"cycle count at {location_id}: saw {sku}, which is not recorded there",
                    task_id=task["id"],
                )
        adjustments = await warehouse.record_count(task["id"], location_id, counts)
        for adj in adjustments:
            await warehouse.raise_exception(
                "inventory_discrepancy",
                f"cycle count at {location_id}: {adj['sku']} system {adj['system']}, counted {adj['counted']}; corrected",
                severity="info", task_id=task["id"], status="resolved",
            )
        return {"counts": counts, "adjustments": adjustments, **detail}

    async def _do_stow(self, task: dict, step: dict) -> dict:
        args = step["args"]
        max_attempts = settings.pick_max_attempts if settings.recovery_enabled else 1
        stowed, failed_attempts = args.get("stowed", 0), 0
        while stowed < args["quantity"]:
            try:
                await self.robot.stow(args["sku"], args["location_id"])
            except RobotError as err:
                failed_attempts += 1
                if failed_attempts >= max_attempts:
                    raise StepFailed(err.code, str(err)) from err
                await record_fault(task["id"], step["seq"], err.code, str(err), f"restow (attempt {failed_attempts + 1})")
                continue
            await warehouse.record_stow(task["id"], self.robot.robot_id, args["sku"], args["location_id"])
            stowed += 1
            await fetch_one(
                "UPDATE task_steps SET args = jsonb_set(args, '{stowed}', to_jsonb(%s::int)) WHERE id = %s RETURNING id",
                stowed, step["id"],
            )
        return {"stowed": stowed, "failed_attempts": failed_attempts}

    async def _do_verify(self, task: dict, step: dict) -> dict:
        mismatches = await warehouse.verify_order(step["args"]["order_id"])
        if mismatches:
            raise StepFailed("order_incomplete", f"order lines not fulfilled: {mismatches}")
        return {"verified": True}

    async def _complete_task(self, task: dict) -> None:
        async with pool.connection() as conn, conn.transaction():
            await conn.execute("UPDATE tasks SET status = 'succeeded', finished_at = now() WHERE id = %s", (task["id"],))
            if task["order_id"]:
                await conn.execute(
                    "UPDATE orders SET status = 'completed', completed_at = now() WHERE id = %s", (task["order_id"],)
                )

    async def _fail_task(self, task: dict, failure: StepFailed) -> None:
        async with pool.connection() as conn, conn.transaction():
            await conn.execute(
                "UPDATE tasks SET status = 'failed', finished_at = now(), error = %s WHERE id = %s",
                (f"{failure.code}: {failure}", task["id"]),
            )
            await conn.execute(
                "UPDATE task_steps SET status = 'skipped' WHERE task_id = %s AND status = 'pending'", (task["id"],)
            )
            await warehouse.release_reservations(conn, task["id"])
            if task["order_id"]:
                await conn.execute("UPDATE orders SET status = 'failed' WHERE id = %s", (task["order_id"],))
        await warehouse.raise_exception(
            failure.code, str(failure), severity="critical", task_id=task["id"], order_id=task["order_id"]
        )

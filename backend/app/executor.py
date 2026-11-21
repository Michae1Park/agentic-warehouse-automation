import asyncio
import logging
from collections.abc import Awaitable, Callable

from psycopg.types.json import Jsonb

from . import warehouse
from .config import settings
from .db import fetch_all, fetch_one, pool
from .robot.gateway import RobotError, RobotGateway

log = logging.getLogger(__name__)

POLL_S = 1.0

TaskCallback = Callable[[dict], Awaitable[None]]


class StepFailed(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


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

        steps = await fetch_all(
            "SELECT * FROM task_steps WHERE task_id = %s AND status <> 'succeeded' ORDER BY seq", task["id"]
        )
        try:
            for step in steps:
                await self._run_step(task, step)
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
            result = await getattr(self, f"_do_{step['action']}")(task, step)
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

    async def _location(self, location_id: str) -> dict:
        location = await fetch_one("SELECT * FROM locations WHERE id = %s", location_id)
        if not location:
            raise StepFailed("unknown_location", f"location {location_id} does not exist")
        return location

    async def _do_navigate(self, task: dict, step: dict) -> dict:
        location = await self._location(step["args"]["location_id"])
        try:
            pose = await self.robot.navigate(float(location["x"]), float(location["y"]))
        except RobotError as err:
            raise StepFailed(err.code, str(err)) from err
        return {"x": pose.x, "y": pose.y}

    async def _do_inspect(self, task: dict, step: dict) -> dict:
        args = step["args"]
        observation = await self.robot.inspect(args["location_id"])
        if args["sku"] not in observation.skus:
            raise StepFailed(
                "item_not_found",
                f"expected {args['sku']} at {args['location_id']}, observed {observation.skus or 'nothing'}",
            )
        return {"observed": observation.skus}

    async def _do_pick(self, task: dict, step: dict) -> dict:
        args = step["args"]
        picked = args.get("picked", 0)
        failed_attempts = 0
        while picked < args["quantity"]:
            try:
                await self.robot.pick(args["sku"], args["location_id"])
            except RobotError as err:
                failed_attempts += 1
                log.warning("pick attempt failed: %s", err)
                if err.code == "item_not_found" or failed_attempts >= settings.pick_max_attempts:
                    raise StepFailed(err.code, str(err)) from err
                continue
            await warehouse.record_pick(task["id"], step["id"], self.robot.robot_id, args["inventory_id"], task["order_id"])
            picked += 1
        return {"picked": picked, "failed_attempts": failed_attempts}

    async def _do_place(self, task: dict, step: dict) -> dict:
        try:
            await self.robot.place(step["args"]["location_id"])
        except RobotError as err:
            raise StepFailed(err.code, str(err)) from err
        return {"placed": await warehouse.record_place(self.robot.robot_id)}

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
            await warehouse.release_reservations(conn, task["id"])
            if task["order_id"]:
                await conn.execute("UPDATE orders SET status = 'failed' WHERE id = %s", (task["order_id"],))
        await warehouse.raise_exception(
            failure.code, str(failure), severity="critical", task_id=task["id"], order_id=task["order_id"]
        )

import asyncio
import math

from ..config import settings
from ..db import fetch_one
from ..sim.world import world
from .gateway import Observation, Pose, RobotError

TICK_S = 0.25


class MockRobot:
    def __init__(self, robot_id: str):
        self.robot_id = robot_id

    async def _sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds / settings.sim_time_scale)

    async def _set_pose(self, pose: Pose) -> None:
        await fetch_one(
            "UPDATE robots SET x = %s, y = %s, theta = %s, updated_at = now() WHERE id = %s RETURNING id",
            pose.x, pose.y, pose.theta, self.robot_id,
        )

    async def navigate(self, x: float, y: float) -> Pose:
        await world.ensure_loaded()
        row = await fetch_one("SELECT x, y FROM robots WHERE id = %s", self.robot_id)
        start = (float(row["x"]), float(row["y"]))
        distance = math.dist(start, (x, y))
        theta = math.atan2(y - start[1], x - start[0]) if distance else 0.0
        blocked_at = world.rng.uniform(0.3, 0.7) if world.path_blocked() else None
        ticks = max(1, math.ceil(distance / settings.mock_robot_speed / TICK_S))
        for i in range(1, ticks + 1):
            t = i / ticks
            await self._sleep(TICK_S)
            await self._set_pose(Pose(start[0] + (x - start[0]) * t, start[1] + (y - start[1]) * t, theta))
            if blocked_at is not None and t >= blocked_at:
                raise RobotError("path_blocked", f"path to ({x}, {y}) blocked by an obstacle")
        return Pose(x, y, theta)

    async def inspect(self, location_id: str) -> Observation:
        await world.ensure_loaded()
        await self._sleep(1.0)
        return Observation(location_id, world.observe(location_id))

    async def pick(self, sku: str, location_id: str, point: list[float] | None = None) -> None:
        await world.ensure_loaded()
        await self._sleep(3.0)
        if world.quantity(location_id, sku) <= 0:
            raise RobotError("item_not_found", f"no {sku} left at {location_id}")
        if world.grasp_fails():
            raise RobotError("grasp_failed", f"grasp of {sku} at {location_id} slipped")
        world.take(location_id, sku)

    async def place(self, location_id: str) -> None:
        await self._sleep(2.0)

    async def release(self, location_id: str) -> None:
        return None

    async def stow(self, sku: str, location_id: str) -> None:
        await self._sleep(3.0)
        if world.grasp_fails():
            raise RobotError("grasp_failed", f"stow of {sku} at {location_id} slipped")
        world.add(location_id, sku)

    async def take_tool(self, tool: str) -> None:
        await self._sleep(2.0)

    async def return_tool(self) -> None:
        await self._sleep(2.0)

    async def push_blocker(self, sku: str, location_id: str) -> dict:
        await self._sleep(4.0)
        return {"pushed": [], "policy": "mock"}

import asyncio
import math

from ..config import settings
from ..db import fetch_all, fetch_one
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
        row = await fetch_one("SELECT x, y FROM robots WHERE id = %s", self.robot_id)
        start = (float(row["x"]), float(row["y"]))
        distance = math.dist(start, (x, y))
        theta = math.atan2(y - start[1], x - start[0])
        ticks = max(1, math.ceil(distance / settings.mock_robot_speed / TICK_S))
        for i in range(1, ticks + 1):
            t = i / ticks
            await self._sleep(TICK_S)
            await self._set_pose(Pose(start[0] + (x - start[0]) * t, start[1] + (y - start[1]) * t, theta))
        return Pose(x, y, theta)

    async def inspect(self, location_id: str) -> Observation:
        await self._sleep(1.0)
        rows = await fetch_all(
            "SELECT sku FROM inventory WHERE location_id = %s AND quantity > 0", location_id
        )
        return Observation(location_id, [r["sku"] for r in rows])

    async def pick(self, sku: str, location_id: str) -> None:
        await self._sleep(3.0)
        row = await fetch_one(
            "SELECT quantity FROM inventory WHERE location_id = %s AND sku = %s", location_id, sku
        )
        if not row or row["quantity"] <= 0:
            raise RobotError("item_not_found", f"no {sku} left at {location_id}")

    async def place(self, location_id: str) -> None:
        await self._sleep(2.0)

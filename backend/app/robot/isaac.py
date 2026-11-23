import asyncio
import base64

import httpx

from ..config import settings
from ..db import fetch_one
from ..sim.world import world
from .gateway import Observation, Pose, RobotError

POSE_SYNC_S = 0.25


class IsaacRobot:
    def __init__(self, robot_id: str):
        self.robot_id = robot_id
        self.client = httpx.AsyncClient(base_url=settings.isaac_url, timeout=600)
        self.last_image: bytes | None = None

    async def _command(self, name: str, payload: dict) -> dict:
        try:
            response = await self.client.post(f"/{name}", json=payload)
        except httpx.HTTPError as err:
            raise RobotError("robot_unreachable", f"simulation not reachable: {err!r}") from err
        body = response.json()
        if response.status_code != 200:
            raise RobotError(body.get("error", "sim_error"), body.get("message", str(body)))
        return body

    async def _sync_pose(self) -> None:
        state = (await self.client.get("/state")).json()
        await fetch_one(
            "UPDATE robots SET x = %s, y = %s, theta = %s, updated_at = now() WHERE id = %s RETURNING id",
            state["x"], state["y"], state["theta"], self.robot_id,
        )

    async def _with_pose_sync(self, name: str, payload: dict) -> dict:
        command = asyncio.create_task(self._command(name, payload))
        while not command.done():
            await asyncio.wait({command}, timeout=POSE_SYNC_S)
            try:
                await self._sync_pose()
            except httpx.HTTPError:
                pass
        return command.result()

    async def navigate_to(self, location_id: str) -> Pose:
        await world.ensure_loaded()
        if world.path_blocked():
            raise RobotError("path_blocked", f"path to {location_id} blocked by an obstacle")
        result = await self._with_pose_sync("navigate", {"location_id": location_id})
        return Pose(result["x"], result["y"], result["theta"])

    async def navigate(self, x: float, y: float) -> Pose:
        raise NotImplementedError("Isaac robot navigates by location id")

    async def inspect(self, location_id: str) -> Observation:
        result = await self._command("inspect", {"location_id": location_id})
        self.last_image = base64.b64decode(result["image_jpeg_b64"])
        return Observation(location_id, result["counts"], self.last_image)

    async def pick(self, sku: str, location_id: str) -> dict:
        await world.ensure_loaded()
        fail = world.quantity(location_id, sku) > 0 and world.grasp_fails()
        result = await self._with_pose_sync(
            "pick", {"sku": sku, "location_id": location_id, "fail": fail, "policy": settings.grasp_policy}
        )
        world.take(location_id, sku)
        return result

    async def place(self, location_id: str) -> None:
        await self._with_pose_sync("place", {"location_id": location_id})

    async def release(self, location_id: str) -> None:
        await self._command("release", {"location_id": location_id})

    async def stow(self, sku: str, location_id: str) -> None:
        fail = world.grasp_fails()
        await self._with_pose_sync("stow", {"sku": sku, "location_id": location_id, "fail": fail})
        world.add(location_id, sku)

    async def reset(self, missing: list[dict], seed: int = 0) -> None:
        await self._command("reset", {"missing": missing, "seed": seed})
        await self._sync_pose()

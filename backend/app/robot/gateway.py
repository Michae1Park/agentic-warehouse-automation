from dataclasses import dataclass
from typing import Protocol


class RobotError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class Pose:
    x: float
    y: float
    theta: float = 0.0


@dataclass
class Observation:
    location_id: str
    counts: dict[str, int]

    @property
    def skus(self) -> list[str]:
        return list(self.counts)


class RobotGateway(Protocol):
    robot_id: str

    async def navigate(self, x: float, y: float) -> Pose: ...

    async def inspect(self, location_id: str) -> Observation: ...

    async def pick(self, sku: str, location_id: str) -> None: ...

    async def place(self, location_id: str) -> None: ...

    async def stow(self, sku: str, location_id: str) -> None: ...

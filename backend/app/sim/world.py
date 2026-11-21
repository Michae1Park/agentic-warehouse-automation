import random
from dataclasses import dataclass, field

from ..db import fetch_all


@dataclass
class FaultConfig:
    grasp_failure_rate: float = 0.0
    nav_block_rate: float = 0.0
    missing_stock_rate: float = 0.0


@dataclass
class PhysicalWorld:
    stock: dict[tuple[str, str], int] = field(default_factory=dict)
    faults: FaultConfig = field(default_factory=FaultConfig)
    rng: random.Random = field(default_factory=random.Random)
    loaded: bool = False

    async def load(self, seed: int | None = None, faults: FaultConfig | None = None) -> list[dict]:
        self.rng = random.Random(seed)
        self.faults = faults or FaultConfig()
        rows = await fetch_all("SELECT location_id, sku, quantity FROM inventory ORDER BY id")
        self.stock = {(r["location_id"], r["sku"]): r["quantity"] for r in rows}
        missing = []
        for key in list(self.stock):
            if self.rng.random() < self.faults.missing_stock_rate:
                self.stock[key] = 0
                missing.append({"location_id": key[0], "sku": key[1]})
        self.loaded = True
        return missing

    async def ensure_loaded(self) -> None:
        if not self.loaded:
            await self.load()

    def observe(self, location_id: str) -> list[str]:
        return [sku for (loc, sku), qty in self.stock.items() if loc == location_id and qty > 0]

    def quantity(self, location_id: str, sku: str) -> int:
        return self.stock.get((location_id, sku), 0)

    def take(self, location_id: str, sku: str) -> None:
        self.stock[(location_id, sku)] -= 1

    def grasp_fails(self) -> bool:
        return self.rng.random() < self.faults.grasp_failure_rate

    def path_blocked(self) -> bool:
        return self.rng.random() < self.faults.nav_block_rate


world = PhysicalWorld()

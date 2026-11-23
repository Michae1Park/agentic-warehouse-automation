import random
from dataclasses import dataclass, field

from ..db import fetch_all


@dataclass
class FaultConfig:
    grasp_failure_rate: float = 0.0
    nav_block_rate: float = 0.0
    missing_stock_rate: float = 0.0
    misplaced_stock_rate: float = 0.0


@dataclass
class PhysicalWorld:
    stock: dict[tuple[str, str], int] = field(default_factory=dict)
    faults: FaultConfig = field(default_factory=FaultConfig)
    rng: random.Random = field(default_factory=random.Random)
    loaded: bool = False
    misplaced: list[dict] = field(default_factory=list)

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
        self.misplaced = await self._misplace()
        self.loaded = True
        return missing

    async def _misplace(self) -> list[dict]:
        if not self.faults.misplaced_stock_rate:
            return []
        pickable = {r["sku"] for r in await fetch_all("SELECT sku FROM products WHERE robot_pickable")}
        bins = [r["id"] for r in await fetch_all("SELECT id FROM locations WHERE kind = 'storage' ORDER BY id")]
        misplaced = []
        for (location_id, sku), qty in list(self.stock.items()):
            if sku not in pickable or location_id not in bins or qty <= 0:
                continue
            if self.rng.random() >= self.faults.misplaced_stock_rate:
                continue
            targets = [b for b in bins if b != location_id and self.stock.get((b, sku), 0) == 0]
            if not targets:
                continue
            target = self.rng.choice(targets)
            self.stock[(location_id, sku)] = 0
            self.stock[(target, sku)] = qty
            misplaced.append({"sku": sku, "from": location_id, "to": target, "quantity": qty})
        return misplaced

    async def ensure_loaded(self) -> None:
        if not self.loaded:
            await self.load()

    def observe(self, location_id: str) -> dict[str, int]:
        return {sku: qty for (loc, sku), qty in self.stock.items() if loc == location_id and qty > 0}

    def quantity(self, location_id: str, sku: str) -> int:
        return self.stock.get((location_id, sku), 0)

    def take(self, location_id: str, sku: str) -> None:
        self.stock[(location_id, sku)] -= 1

    def add(self, location_id: str, sku: str) -> None:
        self.stock[(location_id, sku)] = self.stock.get((location_id, sku), 0) + 1

    def grasp_fails(self) -> bool:
        return self.rng.random() < self.faults.grasp_failure_rate

    def path_blocked(self) -> bool:
        return self.rng.random() < self.faults.nav_block_rate


world = PhysicalWorld()

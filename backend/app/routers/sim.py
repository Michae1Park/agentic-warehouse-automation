import random
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..config import settings
from ..db import fetch_all, fetch_one, pool
from ..sim.world import FaultConfig, world

router = APIRouter(prefix="/sim", tags=["simulation"])

SEED_SQL = Path(settings.seed_sql_path)
TABLES = (
    "agent_messages, agent_runs, task_faults, inventory_transactions, exceptions, task_steps, "
    "tasks, robots, order_lines, orders, inventory, locations, products, events"
)


class ResetRequest(BaseModel):
    seed: int = 0
    generated_orders: int = 0
    faults: FaultConfig = FaultConfig()
    recovery_enabled: bool = True
    route_optimization: bool = True
    time_scale: float = 1.0


def _generate_orders(rng: random.Random, count: int, skus: list[str]) -> list[tuple[int, str, int, str, list]]:
    orders = []
    for i in range(count):
        lines = [(sku, rng.randint(1, 2)) for sku in rng.sample(skus, rng.randint(1, min(3, len(skus))))]
        orders.append((2001 + i, f"Customer {i + 1:03d}", rng.randint(1, 3), rng.choice(["DISPATCH-1", "DISPATCH-2"]), lines))
    return orders


@router.post("/reset")
async def reset(request: ResetRequest, http_request: Request):
    busy = await fetch_one("SELECT id FROM tasks WHERE status IN ('queued', 'running') LIMIT 1")
    if busy:
        raise HTTPException(409, f"task {busy['id']} is still active")

    async with pool.connection() as conn, conn.transaction():
        await conn.execute(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE")
        await conn.execute(SEED_SQL.read_text())
        if request.generated_orders:
            rng = random.Random(request.seed)
            await conn.execute("DELETE FROM orders")
            cur = await conn.execute("SELECT sku FROM products ORDER BY sku")
            skus = [r["sku"] for r in await cur.fetchall()]
            for order_id, customer, priority, dispatch, lines in _generate_orders(rng, request.generated_orders, skus):
                await conn.execute(
                    "INSERT INTO orders (id, customer, priority, dispatch_location) VALUES (%s, %s, %s, %s)",
                    (order_id, customer, priority, dispatch),
                )
                for sku, qty in lines:
                    await conn.execute(
                        "INSERT INTO order_lines (order_id, sku, quantity) VALUES (%s, %s, %s)", (order_id, sku, qty)
                    )

    settings.recovery_enabled = request.recovery_enabled
    settings.route_optimization = request.route_optimization
    settings.sim_time_scale = request.time_scale
    missing = await world.load(request.seed, request.faults)
    for robot in http_request.app.state.robots.values():
        if hasattr(robot, "reset"):
            await robot.reset(missing, request.seed)
    return {"status": "reset", "missing_stock": missing}


@router.get("/inventory-accuracy")
async def inventory_accuracy():
    await world.ensure_loaded()
    rows = await fetch_all("SELECT location_id, sku, quantity FROM inventory")
    mismatches = [
        {**r, "physical": world.quantity(r["location_id"], r["sku"])}
        for r in rows
        if world.quantity(r["location_id"], r["sku"]) != r["quantity"]
    ]
    return {
        "records": len(rows),
        "accurate": len(rows) - len(mismatches),
        "accuracy": (len(rows) - len(mismatches)) / len(rows) if rows else 1.0,
        "mismatches": mismatches,
    }

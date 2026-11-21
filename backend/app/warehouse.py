import math
from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from .db import fetch_all, fetch_one, pool


class WarehouseError(Exception):
    pass


async def get_order(order_id: int) -> dict | None:
    order = await fetch_one(
        "SELECT id, customer, status, priority, dispatch_location FROM orders WHERE id = %s", order_id
    )
    if order:
        order["lines"] = await fetch_all(
            """
            SELECT l.sku, p.name, l.quantity, l.picked_qty
            FROM order_lines l JOIN products p ON p.sku = l.sku
            WHERE l.order_id = %s ORDER BY l.id
            """,
            order_id,
        )
    return order


async def find_stock(sku: str) -> list[dict]:
    return await fetch_all(
        """
        SELECT i.location_id, i.quantity, i.reserved, i.quantity - i.reserved AS available
        FROM inventory i WHERE i.sku = %s ORDER BY available DESC
        """,
        sku,
    )


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.dist(a, b)


def _sequence_stops(start: tuple[float, float], stops: list[dict]) -> list[dict]:
    remaining, ordered, here = list(stops), [], start
    while remaining:
        nearest = min(remaining, key=lambda s: _distance(here, (s["x"], s["y"])))
        remaining.remove(nearest)
        ordered.append(nearest)
        here = (nearest["x"], nearest["y"])
    return ordered


async def _allocate(conn: AsyncConnection, sku: str, needed: int) -> tuple[list[dict], int]:
    cur = await conn.execute(
        """
        SELECT i.id, i.location_id, i.quantity - i.reserved AS available, l.x, l.y
        FROM inventory i JOIN locations l ON l.id = i.location_id
        WHERE i.sku = %s AND i.quantity > i.reserved
        ORDER BY available DESC
        FOR UPDATE OF i
        """,
        (sku,),
    )
    allocations = []
    for row in await cur.fetchall():
        if needed == 0:
            break
        take = min(needed, row["available"])
        allocations.append(
            {"inventory_id": row["id"], "sku": sku, "location_id": row["location_id"], "quantity": take,
             "x": float(row["x"]), "y": float(row["y"])}
        )
        needed -= take
    return allocations, needed


async def plan_fulfillment(
    order_id: int, robot_id: str, dispatch_location: str | None, conversation_id: str | None
) -> dict[str, Any]:
    async with pool.connection() as conn, conn.transaction():
        cur = await conn.execute("SELECT * FROM orders WHERE id = %s FOR UPDATE", (order_id,))
        order = await cur.fetchone()
        if not order:
            raise WarehouseError(f"order {order_id} does not exist")
        if order["status"] != "pending":
            raise WarehouseError(f"order {order_id} is {order['status']}, only pending orders can be planned")

        dispatch_id = dispatch_location or order["dispatch_location"]
        cur = await conn.execute("SELECT * FROM locations WHERE id = %s AND kind = 'dispatch'", (dispatch_id,))
        dispatch = await cur.fetchone()
        if not dispatch:
            raise WarehouseError(f"{dispatch_id} is not a dispatch location")

        cur = await conn.execute("SELECT x, y FROM robots WHERE id = %s", (robot_id,))
        robot = await cur.fetchone()
        if not robot:
            raise WarehouseError(f"robot {robot_id} does not exist")

        cur = await conn.execute(
            "SELECT sku, quantity - picked_qty AS needed FROM order_lines WHERE order_id = %s ORDER BY id",
            (order_id,),
        )
        allocations, shortages = [], []
        for line in await cur.fetchall():
            allocated, short = await _allocate(conn, line["sku"], line["needed"])
            allocations += allocated
            if short:
                shortages.append({"sku": line["sku"], "requested": line["needed"], "short_by": short})
        if shortages:
            raise WarehouseError(f"insufficient stock for order {order_id}: {shortages}")

        for alloc in allocations:
            await conn.execute(
                "UPDATE inventory SET reserved = reserved + %s, updated_at = now() WHERE id = %s",
                (alloc["quantity"], alloc["inventory_id"]),
            )

        stops = _sequence_stops((float(robot["x"]), float(robot["y"])), allocations)
        steps = []
        for stop in stops:
            steps += [
                ("navigate", {"location_id": stop["location_id"]}),
                ("inspect", {"location_id": stop["location_id"], "sku": stop["sku"]}),
                ("pick", {"location_id": stop["location_id"], "sku": stop["sku"], "quantity": stop["quantity"],
                          "inventory_id": stop["inventory_id"]}),
            ]
        steps += [
            ("navigate", {"location_id": dispatch_id}),
            ("place", {"location_id": dispatch_id}),
            ("verify", {"order_id": order_id}),
        ]

        plan = {"dispatch_location": dispatch_id, "picks": [
            {k: s[k] for k in ("sku", "location_id", "quantity")} for s in stops
        ]}
        cur = await conn.execute(
            """
            INSERT INTO tasks (order_id, robot_id, instruction, conversation_id, plan)
            VALUES (%s, %s, %s, %s, %s) RETURNING id
            """,
            (order_id, robot_id, f"fulfill order {order_id}", conversation_id, Jsonb(plan)),
        )
        task_id = (await cur.fetchone())["id"]
        for seq, (action, args) in enumerate(steps, start=1):
            await conn.execute(
                "INSERT INTO task_steps (task_id, seq, action, args) VALUES (%s, %s, %s, %s)",
                (task_id, seq, action, Jsonb(args)),
            )

    return {"task_id": task_id, "order_id": order_id, "robot_id": robot_id, **plan,
            "steps": [f"{a} {' '.join(str(v) for k, v in args.items() if k != 'inventory_id')}" for a, args in steps]}


async def dispatch_task(task_id: int) -> dict:
    task = await fetch_one(
        "UPDATE tasks SET status = 'queued' WHERE id = %s AND status = 'planned' RETURNING id, robot_id, status",
        task_id,
    )
    if not task:
        raise WarehouseError(f"task {task_id} is not in planned state")
    return task


async def release_reservations(conn: AsyncConnection, task_id: int) -> None:
    cur = await conn.execute(
        "SELECT args FROM task_steps WHERE task_id = %s AND action = 'pick' AND status <> 'succeeded'", (task_id,)
    )
    for step in await cur.fetchall():
        args = step["args"]
        remaining = args["quantity"] - args.get("picked", 0)
        if remaining > 0:
            await conn.execute(
                "UPDATE inventory SET reserved = GREATEST(reserved - %s, 0), updated_at = now() WHERE id = %s",
                (remaining, args["inventory_id"]),
            )


async def cancel_task(task_id: int) -> dict:
    async with pool.connection() as conn, conn.transaction():
        cur = await conn.execute(
            "UPDATE tasks SET status = 'cancelled', finished_at = now() "
            "WHERE id = %s AND status IN ('planned', 'queued') RETURNING id, order_id",
            (task_id,),
        )
        task = await cur.fetchone()
        if not task:
            raise WarehouseError(f"task {task_id} cannot be cancelled (only planned or queued tasks)")
        await release_reservations(conn, task_id)
    return {"task_id": task_id, "status": "cancelled"}


async def record_pick(task_id: int, step_id: int, robot_id: str, inventory_id: int, order_id: int) -> None:
    async with pool.connection() as conn, conn.transaction():
        cur = await conn.execute(
            """
            UPDATE inventory SET quantity = quantity - 1, reserved = reserved - 1, updated_at = now()
            WHERE id = %s RETURNING sku, location_id
            """,
            (inventory_id,),
        )
        item = await cur.fetchone()
        await conn.execute(
            "INSERT INTO inventory_transactions (sku, location_id, delta, reason, task_id) VALUES (%s, %s, -1, 'pick', %s)",
            (item["sku"], item["location_id"], task_id),
        )
        await conn.execute(
            "UPDATE order_lines SET picked_qty = picked_qty + 1 WHERE order_id = %s AND sku = %s",
            (order_id, item["sku"]),
        )
        await conn.execute(
            "UPDATE robots SET tote = tote || %s, updated_at = now() WHERE id = %s",
            (Jsonb([item["sku"]]), robot_id),
        )
        await conn.execute(
            "UPDATE task_steps SET args = jsonb_set(args, '{picked}', to_jsonb(COALESCE((args->>'picked')::int, 0) + 1)) "
            "WHERE id = %s",
            (step_id,),
        )


async def record_place(robot_id: str) -> list[str]:
    row = await fetch_one(
        "UPDATE robots r SET tote = '[]', updated_at = now() FROM robots old "
        "WHERE r.id = old.id AND r.id = %s RETURNING old.tote",
        robot_id,
    )
    return row["tote"]


async def verify_order(order_id: int) -> list[dict]:
    return await fetch_all(
        "SELECT sku, quantity, picked_qty FROM order_lines WHERE order_id = %s AND picked_qty <> quantity", order_id
    )


async def raise_exception(
    type_: str, message: str, severity: str = "warning", task_id: int | None = None, order_id: int | None = None
) -> dict:
    return await fetch_one(
        "INSERT INTO exceptions (type, message, severity, task_id, order_id) VALUES (%s, %s, %s, %s, %s) RETURNING *",
        type_, message, severity, task_id, order_id,
    )

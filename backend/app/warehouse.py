import math
import re
from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from .config import settings
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


STOP_WORDS = {"the", "a", "an", "of", "with", "and", "some", "one", "two", "three", "units", "unit", "please"}


async def resolve_product(query: str) -> dict:
    text = query.strip()
    rows = await fetch_all(
        "SELECT sku, name FROM products WHERE upper(sku) = upper(%s) OR lower(name) = lower(%s)", text, text
    )
    if not rows:
        rows = await fetch_all("SELECT sku, name FROM products WHERE name ILIKE %s ORDER BY sku", f"%{text}%")
    if not rows:
        rows = await _match_description(text)
    if len(rows) == 1:
        return rows[0]
    if rows:
        return {"error": f"'{query}' matches several products", "candidates": rows}
    return {"error": f"no product matches '{query}'"}


async def _match_description(text: str) -> list[dict]:
    words = set(re.findall(r"[a-z]+", text.lower())) - STOP_WORDS
    products = await fetch_all("SELECT sku, name, color, category, description FROM products ORDER BY sku")
    scored = []
    for product in products:
        vocabulary = set(re.findall(r"[a-z]+", " ".join(
            [product["name"], product["color"], product["category"], product["description"]]).lower()))
        scored.append((len(words & vocabulary), product))
    best = max((score for score, _ in scored), default=0)
    return [{"sku": p["sku"], "name": p["name"]} for score, p in scored if best and score == best]


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


async def _allocate(conn: AsyncConnection, sku: str, needed: int,
                    include_receiving: bool = False) -> tuple[list[dict], int]:
    kinds = ["storage", "staging"] if include_receiving else ["storage"]
    cur = await conn.execute(
        """
        SELECT i.id, i.location_id, i.quantity - i.reserved AS available, l.x, l.y
        FROM inventory i JOIN locations l ON l.id = i.location_id
        WHERE i.sku = %s AND i.quantity > i.reserved AND l.kind = ANY(%s)
        ORDER BY l.kind = 'staging', available DESC
        FOR UPDATE OF i
        """,
        (sku, kinds),
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


async def _ensure_pickable(conn: AsyncConnection, sku: str) -> None:
    cur = await conn.execute("SELECT name, robot_pickable FROM products WHERE sku = %s", (sku,))
    product = await cur.fetchone()
    if product and not product["robot_pickable"]:
        raise WarehouseError(f"{sku} ({product['name']}) is not robot-pickable; it must be handled manually")


async def plan_fulfillment(
    order_id: int, robot_id: str, dispatch_location: str | None, conversation_id: str | None,
    include_receiving: bool = False,
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
            await _ensure_pickable(conn, line["sku"])
            allocated, short = await _allocate(conn, line["sku"], line["needed"], include_receiving)
            allocations += allocated
            if short:
                cur = await conn.execute(
                    "SELECT COALESCE(SUM(i.quantity - i.reserved), 0) AS n FROM inventory i "
                    "JOIN locations l ON l.id = i.location_id WHERE i.sku = %s AND l.kind = 'staging'",
                    (line["sku"],),
                )
                in_receiving = 0 if include_receiving else int((await cur.fetchone())["n"])
                shortages.append({"sku": line["sku"], "requested": line["needed"], "short_by": short,
                                  "in_receiving": in_receiving})
        if shortages:
            raise WarehouseError(f"insufficient stock for order {order_id}: {shortages}")

        for alloc in allocations:
            await conn.execute(
                "UPDATE inventory SET reserved = reserved + %s, updated_at = now() WHERE id = %s",
                (alloc["quantity"], alloc["inventory_id"]),
            )

        start = (float(robot["x"]), float(robot["y"]))
        stops = _sequence_stops(start, allocations) if settings.route_optimization else allocations
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


async def clear_blocker(task_id: int) -> dict:
    async with pool.connection() as conn, conn.transaction():
        cur = await conn.execute("SELECT status, error FROM tasks WHERE id = %s FOR UPDATE", (task_id,))
        task = await cur.fetchone()
        if not task or task["status"] != "planned" or not (task["error"] or "").startswith("blocked"):
            raise WarehouseError(f"task {task_id} is not paused on a blocked pick")
        cur = await conn.execute(
            "SELECT seq, args FROM task_steps WHERE task_id = %s AND status = 'pending' AND action = 'pick' ORDER BY seq LIMIT 1",
            (task_id,),
        )
        pick = await cur.fetchone()
        await conn.execute("UPDATE task_steps SET seq = seq + 1000 WHERE task_id = %s AND seq >= %s", (task_id, pick["seq"]))
        await conn.execute("UPDATE task_steps SET seq = seq - 997 WHERE task_id = %s AND seq >= 1000", (task_id,))
        target = {"sku": pick["args"]["sku"], "location_id": pick["args"]["location_id"]}
        for offset, (action, args) in enumerate([("take_tool", {"tool": "paddle"}), ("push_blocker", target), ("return_tool", {})]):
            await conn.execute("INSERT INTO task_steps (task_id, seq, action, args) VALUES (%s, %s, %s, %s)",
                               (task_id, pick["seq"] + offset, action, Jsonb(args)))
        await conn.execute("UPDATE tasks SET error = NULL WHERE id = %s", (task_id,))
    return {"task_id": task_id, "inserted": ["take_tool paddle", f"push_blocker {target['sku']} at {target['location_id']}",
                                             "return_tool"], "next": "dispatch_task to resume"}


async def release_reservations(conn: AsyncConnection, task_id: int) -> None:
    cur = await conn.execute(
        "SELECT args FROM task_steps WHERE task_id = %s AND action = 'pick' AND status IN ('pending', 'running', 'failed')",
        (task_id,),
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


async def record_pick(task_id: int, step_id: int, robot_id: str, inventory_id: int, order_id: int | None) -> None:
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
        if order_id:
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
    type_: str, message: str, severity: str = "warning", task_id: int | None = None, order_id: int | None = None,
    status: str = "open",
) -> dict:
    return await fetch_one(
        """
        INSERT INTO exceptions (type, message, severity, task_id, order_id, status, resolution, resolved_at)
        VALUES (%s, %s, %s, %s, %s, %s, CASE WHEN %s = 'resolved' THEN 'auto-recovered' END,
                CASE WHEN %s = 'resolved' THEN now() END)
        RETURNING *
        """,
        type_, message, severity, task_id, order_id, status, status, status,
    )


async def _cycle_count_to_zero(conn: AsyncConnection, inventory_id: int, task_id: int) -> None:
    cur = await conn.execute(
        "SELECT sku, location_id, quantity FROM inventory WHERE id = %s FOR UPDATE", (inventory_id,)
    )
    row = await cur.fetchone()
    if row["quantity"] == 0:
        return
    await conn.execute(
        "UPDATE inventory SET quantity = 0, reserved = 0, updated_at = now() WHERE id = %s", (inventory_id,)
    )
    await conn.execute(
        "INSERT INTO inventory_transactions (sku, location_id, delta, reason, task_id) "
        "VALUES (%s, %s, %s, 'cycle_count', %s)",
        (row["sku"], row["location_id"], -row["quantity"], task_id),
    )


async def record_missing_stock(task_id: int, failed_step_id: int) -> dict:
    async with pool.connection() as conn, conn.transaction():
        cur = await conn.execute("SELECT * FROM task_steps WHERE id = %s", (failed_step_id,))
        failed = await cur.fetchone()
        cur = await conn.execute(
            """
            SELECT * FROM task_steps
            WHERE task_id = %s AND action = 'pick' AND seq >= %s
              AND args->>'location_id' = %s AND args->>'sku' = %s
            ORDER BY seq LIMIT 1
            """,
            (task_id, failed["seq"], failed["args"]["location_id"], failed["args"]["sku"]),
        )
        pick = await cur.fetchone()
        await _cycle_count_to_zero(conn, pick["args"]["inventory_id"], task_id)
        await conn.execute("UPDATE task_steps SET status = 'skipped' WHERE id = ANY(%s)", ([pick["id"], failed["id"]],))
        await conn.execute("UPDATE task_steps SET args = args || '{\"missing\": true}' WHERE id = %s", (pick["id"],))
    return pick


async def reallocate_missing_stock(task_id: int, include_receiving: bool = False) -> list[str] | None:
    async with pool.connection() as conn, conn.transaction():
        cur = await conn.execute(
            "SELECT * FROM task_steps WHERE task_id = %s AND action = 'pick' AND status = 'skipped' "
            "AND args->>'missing' = 'true' AND NOT args ? 'reallocated' ORDER BY seq LIMIT 1 FOR UPDATE",
            (task_id,),
        )
        pick = await cur.fetchone()
        if not pick:
            raise WarehouseError(f"task {task_id} has no pick waiting for reallocation")
        remaining = pick["args"]["quantity"] - pick["args"].get("picked", 0)
        allocations, short = await _allocate(conn, pick["args"]["sku"], remaining, include_receiving)
        if short:
            return None
        for alloc in allocations:
            await conn.execute(
                "UPDATE inventory SET reserved = reserved + %s, updated_at = now() WHERE id = %s",
                (alloc["quantity"], alloc["inventory_id"]),
            )
        new_steps = []
        for alloc in allocations:
            new_steps += [
                ("navigate", {"location_id": alloc["location_id"]}),
                ("inspect", {"location_id": alloc["location_id"], "sku": alloc["sku"]}),
                ("pick", {"location_id": alloc["location_id"], "sku": alloc["sku"], "quantity": alloc["quantity"],
                          "inventory_id": alloc["inventory_id"]}),
            ]
        shift = len(new_steps)
        await conn.execute(
            "UPDATE task_steps SET seq = -(seq + %s) WHERE task_id = %s AND seq > %s", (shift, task_id, pick["seq"])
        )
        await conn.execute("UPDATE task_steps SET seq = -seq WHERE task_id = %s AND seq < 0", (task_id,))
        for offset, (action, args) in enumerate(new_steps, start=1):
            await conn.execute(
                "INSERT INTO task_steps (task_id, seq, action, args) VALUES (%s, %s, %s, %s)",
                (task_id, pick["seq"] + offset, action, Jsonb(args)),
            )
        await conn.execute("UPDATE task_steps SET args = args || '{\"reallocated\": true}' WHERE id = %s", (pick["id"],))
        await conn.execute("UPDATE tasks SET error = NULL WHERE id = %s", (task_id,))
    return [a["location_id"] for a in allocations]


async def reallocate_pick(task_id: int, include_receiving: bool = False) -> dict:
    task = await fetch_one("SELECT status, error FROM tasks WHERE id = %s", task_id)
    if not task or task["status"] != "planned" or not (task["error"] or "").startswith("item_not_found"):
        raise WarehouseError(f"task {task_id} is not paused on missing stock")
    sources = await reallocate_missing_stock(task_id, include_receiving)
    if sources is None:
        holders = await fetch_all(
            """
            SELECT t.id AS task_id, t.kind, s.args->>'location_id' AS location_id, (s.args->>'quantity')::int AS quantity
            FROM task_steps s JOIN tasks t ON t.id = s.task_id
            WHERE s.action = 'pick' AND s.status = 'pending' AND t.status IN ('planned', 'queued') AND t.id <> %s
              AND s.args->>'sku' = (SELECT args->>'sku' FROM task_steps WHERE task_id = %s AND args->>'missing' = 'true'
                                    ORDER BY seq LIMIT 1)
            """,
            task_id, task_id,
        )
        held = "; ".join(f"{h['quantity']} at {h['location_id']} reserved by {h['kind']} task {h['task_id']}"
                         for h in holders)
        raise WarehouseError(f"no unreserved stock covers the missing units for task {task_id}"
                             + ("" if include_receiving else "; receiving was not included")
                             + (f"; held by queued work: {held}" if held else ""))
    left = await fetch_one(
        "SELECT COALESCE(SUM(i.quantity - i.reserved), 0) AS n FROM inventory i JOIN locations l ON l.id = i.location_id "
        "WHERE l.kind = 'staging' AND i.sku = (SELECT args->>'sku' FROM task_steps WHERE task_id = %s "
        "AND args->>'missing' = 'true' ORDER BY seq LIMIT 1)",
        task_id,
    )
    return {"task_id": task_id, "rerouted_to": sources, "unreserved_in_receiving": int(left["n"]),
            "next": "dispatch_task to resume"}


async def _create_task(
    conn: AsyncConnection, kind: str, instruction: str, robot_id: str, conversation_id: str | None,
    plan: dict, steps: list[tuple[str, dict]], order_id: int | None = None,
) -> int:
    cur = await conn.execute(
        """
        INSERT INTO tasks (kind, order_id, robot_id, instruction, conversation_id, plan)
        VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (kind, order_id, robot_id, instruction, conversation_id, Jsonb(plan)),
    )
    task_id = (await cur.fetchone())["id"]
    for seq, (action, args) in enumerate(steps, start=1):
        await conn.execute(
            "INSERT INTO task_steps (task_id, seq, action, args) VALUES (%s, %s, %s, %s)",
            (task_id, seq, action, Jsonb(args)),
        )
    return task_id


async def _robot_position(conn: AsyncConnection, robot_id: str) -> tuple[float, float]:
    cur = await conn.execute("SELECT x, y FROM robots WHERE id = %s", (robot_id,))
    robot = await cur.fetchone()
    if not robot:
        raise WarehouseError(f"robot {robot_id} does not exist")
    return float(robot["x"]), float(robot["y"])


async def plan_inspection(location_ids: list[str], robot_id: str, conversation_id: str | None) -> dict:
    async with pool.connection() as conn, conn.transaction():
        cur = await conn.execute(
            "SELECT id AS location_id, x, y FROM locations WHERE id = ANY(%s) AND kind IN ('storage', 'staging')",
            (location_ids,),
        )
        found = [{**r, "x": float(r["x"]), "y": float(r["y"])} for r in await cur.fetchall()]
        unknown = set(location_ids) - {r["location_id"] for r in found}
        if unknown:
            raise WarehouseError(f"unknown or non-storage locations: {sorted(unknown)}")
        stops = _sequence_stops(await _robot_position(conn, robot_id), found)
        steps = []
        for stop in stops:
            steps += [("navigate", {"location_id": stop["location_id"]}), ("count", {"location_id": stop["location_id"]})]
        route = [s["location_id"] for s in stops]
        task_id = await _create_task(
            conn, "inspection", f"cycle count {len(route)} locations", robot_id, conversation_id, {"route": route}, steps
        )
    return {"task_id": task_id, "kind": "inspection", "route": route}


async def plan_relocation(
    sku: str, from_location: str, to_location: str, quantity: int, robot_id: str,
    conversation_id: str | None, kind: str = "relocation",
) -> dict:
    if quantity <= 0:
        raise WarehouseError("quantity must be positive")
    async with pool.connection() as conn, conn.transaction():
        await _ensure_pickable(conn, sku)
        cur = await conn.execute(
            "SELECT * FROM locations WHERE id = %s AND kind IN ('storage', 'staging')", (to_location,)
        )
        if not await cur.fetchone():
            raise WarehouseError(f"{to_location} is not a storage location")
        cur = await conn.execute(
            "SELECT id, quantity - reserved AS available FROM inventory WHERE sku = %s AND location_id = %s FOR UPDATE",
            (sku, from_location),
        )
        source = await cur.fetchone()
        if not source or source["available"] < quantity:
            available = source["available"] if source else 0
            raise WarehouseError(f"only {available} unreserved {sku} at {from_location}, requested {quantity}")
        await conn.execute(
            "UPDATE inventory SET reserved = reserved + %s, updated_at = now() WHERE id = %s", (quantity, source["id"])
        )
        steps = [
            ("navigate", {"location_id": from_location}),
            ("inspect", {"location_id": from_location, "sku": sku}),
            ("pick", {"location_id": from_location, "sku": sku, "quantity": quantity, "inventory_id": source["id"]}),
            ("navigate", {"location_id": to_location}),
            ("stow", {"location_id": to_location, "sku": sku, "quantity": quantity}),
        ]
        plan = {"sku": sku, "from": from_location, "to": to_location, "quantity": quantity}
        task_id = await _create_task(
            conn, kind, f"{kind} {quantity}x {sku} {from_location} -> {to_location}", robot_id, conversation_id, plan, steps
        )
    return {"task_id": task_id, "kind": kind, **plan}


async def record_count(task_id: int, location_id: str, counts: dict[str, int]) -> list[dict]:
    adjustments = []
    async with pool.connection() as conn, conn.transaction():
        cur = await conn.execute(
            "SELECT id, sku, quantity, reserved FROM inventory WHERE location_id = %s FOR UPDATE", (location_id,)
        )
        recorded = {r["sku"]: r for r in await cur.fetchall()}
        for sku in set(recorded) | set(counts):
            counted = counts.get(sku, 0)
            row = recorded.get(sku)
            system = row["quantity"] if row else 0
            if counted == system:
                continue
            if row:
                await conn.execute(
                    "UPDATE inventory SET quantity = %s, reserved = LEAST(reserved, %s), updated_at = now() WHERE id = %s",
                    (counted, counted, row["id"]),
                )
            else:
                await conn.execute(
                    "INSERT INTO inventory (sku, location_id, quantity) VALUES (%s, %s, %s)", (sku, location_id, counted)
                )
            await conn.execute(
                "INSERT INTO inventory_transactions (sku, location_id, delta, reason, task_id) "
                "VALUES (%s, %s, %s, 'cycle_count', %s)",
                (sku, location_id, counted - system, task_id),
            )
            adjustments.append({"sku": sku, "location_id": location_id, "system": system, "counted": counted})
    return adjustments


async def record_found_stock(task_id: int, location_id: str, found: dict[str, int]) -> list[dict]:
    rows = []
    async with pool.connection() as conn, conn.transaction():
        for sku, quantity in found.items():
            await conn.execute(
                """
                INSERT INTO inventory (sku, location_id, quantity) VALUES (%s, %s, %s)
                ON CONFLICT (sku, location_id) DO UPDATE SET quantity = EXCLUDED.quantity, updated_at = now()
                """,
                (sku, location_id, quantity),
            )
            await conn.execute(
                "INSERT INTO inventory_transactions (sku, location_id, delta, reason, task_id) "
                "VALUES (%s, %s, %s, 'cycle_count', %s)",
                (sku, location_id, quantity, task_id),
            )
            rows.append({"sku": sku, "location_id": location_id, "system": 0, "counted": quantity})
    return rows


async def record_stow(task_id: int, robot_id: str, sku: str, location_id: str) -> None:
    async with pool.connection() as conn, conn.transaction():
        await conn.execute(
            """
            UPDATE robots SET tote = tote - (
                SELECT (ordinality - 1)::int FROM jsonb_array_elements_text(tote) WITH ORDINALITY
                WHERE value = %s LIMIT 1
            ), updated_at = now()
            WHERE id = %s
            """,
            (sku, robot_id),
        )
        await conn.execute(
            """
            INSERT INTO inventory (sku, location_id, quantity) VALUES (%s, %s, 1)
            ON CONFLICT (sku, location_id) DO UPDATE SET quantity = inventory.quantity + 1, updated_at = now()
            """,
            (sku, location_id),
        )
        await conn.execute(
            "INSERT INTO inventory_transactions (sku, location_id, delta, reason, task_id) VALUES (%s, %s, 1, 'stow', %s)",
            (sku, location_id, task_id),
        )


async def presence_counts(location_id: str, seen: dict[str, int]) -> tuple[dict[str, int], list[str]]:
    rows = await fetch_all("SELECT sku, quantity FROM inventory WHERE location_id = %s AND quantity > 0", location_id)
    recorded = {r["sku"]: r["quantity"] for r in rows}
    counts = {sku: qty for sku, qty in recorded.items() if sku in seen}
    return counts, sorted(set(seen) - set(recorded))

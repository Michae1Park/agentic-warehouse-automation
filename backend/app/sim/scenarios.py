from psycopg import AsyncConnection

from .world import PhysicalWorld

SHORTAGE_ORDER, SHORTAGE_SKU, SHORTAGE_SHELF = 1044, "SKU-1001", "A-01-1"
MUSTARD, SOUP, CRACKERS = "SKU-1001", "SKU-1002", "SKU-2001"
STORAGE = "location_id IN (SELECT id FROM locations WHERE kind = 'storage')"


async def _set_stock(conn: AsyncConnection, sku: str, shelves: dict[str, int], receiving: int | None = None) -> None:
    await conn.execute(f"UPDATE inventory SET quantity = 0 WHERE sku = %s AND {STORAGE}", (sku,))
    for location_id, quantity in shelves.items():
        await conn.execute(
            "INSERT INTO inventory (location_id, sku, quantity) VALUES (%s, %s, %s) "
            "ON CONFLICT (location_id, sku) DO UPDATE SET quantity = EXCLUDED.quantity",
            (location_id, sku, quantity),
        )
    if receiving is not None:
        await conn.execute("UPDATE inventory SET quantity = %s WHERE sku = %s AND location_id = 'STAGING-1'",
                           (receiving, sku))


async def _set_order(conn: AsyncConnection, order_id: int, priority: int, dispatch: str, lines: dict[str, int],
                     customer: str | None = None) -> None:
    await conn.execute(
        "INSERT INTO orders (id, customer, priority, dispatch_location) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (id) DO UPDATE SET priority = EXCLUDED.priority, dispatch_location = EXCLUDED.dispatch_location",
        (order_id, customer or f"Customer {order_id}", priority, dispatch),
    )
    await conn.execute("DELETE FROM order_lines WHERE order_id = %s", (order_id,))
    for sku, quantity in lines.items():
        await conn.execute("INSERT INTO order_lines (order_id, sku, quantity) VALUES (%s, %s, %s)",
                           (order_id, sku, quantity))


async def _shortage(conn: AsyncConnection) -> None:
    await conn.execute(
        f"UPDATE inventory SET quantity = CASE WHEN location_id = %s THEN 1 ELSE 0 END WHERE sku = %s AND {STORAGE}",
        (SHORTAGE_SHELF, SHORTAGE_SKU),
    )
    await conn.execute("UPDATE order_lines SET quantity = 3 WHERE order_id = %s AND sku = %s",
                       (SHORTAGE_ORDER, SHORTAGE_SKU))


async def _batch(conn: AsyncConnection) -> None:
    await _set_order(conn, 1044, 2, "DISPATCH-1", {MUSTARD: 2})
    await _set_order(conn, 1046, 2, "DISPATCH-1", {SOUP: 1}, "Woodgrove Market")
    await _set_order(conn, 1047, 3, "DISPATCH-1", {MUSTARD: 1}, "Proseware Cafe")


async def _competing(conn: AsyncConnection) -> None:
    await _set_stock(conn, MUSTARD, {"A-01-1": 2}, receiving=0)


async def _end_of_shift(conn: AsyncConnection) -> None:
    await _set_stock(conn, MUSTARD, {"A-01-1": 2, "B-01-1": 1}, receiving=4)
    await _set_stock(conn, SOUP, {"A-02-1": 3}, receiving=0)
    await _set_order(conn, 1042, 1, "DISPATCH-1", {MUSTARD: 2, SOUP: 1})
    await _set_order(conn, 1045, 1, "DISPATCH-1", {SOUP: 2})
    await _set_order(conn, 1044, 2, "DISPATCH-1", {MUSTARD: 3})
    await _set_order(conn, 1040, 3, "DISPATCH-1", {MUSTARD: 1, SOUP: 2})
    await _set_order(conn, 1046, 2, "DISPATCH-1", {CRACKERS: 1}, "Woodgrove Market")


SETUPS = {"shortage": _shortage, "batch": _batch, "competing": _competing, "end_of_shift": _end_of_shift}
TRIAGE_MISPLACED = (SOUP, "C-01-1", "C-02-1")
TRIAGE_MISSING = ("C-04-1", MUSTARD)


async def setup(conn: AsyncConnection, scenario: str) -> None:
    if scenario in SETUPS:
        await SETUPS[scenario](conn)


def physical_faults(world: PhysicalWorld, scenario: str) -> list[dict]:
    if scenario != "triage":
        return []
    sku, home, found = TRIAGE_MISPLACED
    quantity = world.stock.get((home, sku), 0)
    world.stock[(home, sku)] = 0
    world.stock[(found, sku)] = world.stock.get((found, sku), 0) + quantity
    world.misplaced = [{"sku": sku, "from": home, "to": found, "quantity": quantity}]
    world.stock[TRIAGE_MISSING] = 0
    return [{"location_id": TRIAGE_MISSING[0], "sku": TRIAGE_MISSING[1]}]

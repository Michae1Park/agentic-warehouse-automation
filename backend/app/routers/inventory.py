from fastapi import APIRouter

from ..db import fetch_all

router = APIRouter(prefix="/inventory", tags=["inventory"])


@router.get("")
async def inventory_levels():
    return await fetch_all("SELECT * FROM inventory_levels ORDER BY sku")


@router.get("/locations")
async def stock_by_location(sku: str | None = None):
    where = "WHERE i.sku = %s" if sku else ""
    params = (sku,) if sku else ()
    return await fetch_all(
        f"""
        SELECT i.sku, p.name, i.location_id, l.aisle, l.bay, l.level, l.x, l.y, l.z,
               i.quantity, i.reserved, i.updated_at
        FROM inventory i
        JOIN products p ON p.sku = i.sku
        JOIN locations l ON l.id = i.location_id
        {where}
        ORDER BY i.sku, i.location_id
        """,
        *params,
    )


@router.get("/transactions")
async def transactions(limit: int = 50):
    return await fetch_all("SELECT * FROM inventory_transactions ORDER BY id DESC LIMIT %s", limit)

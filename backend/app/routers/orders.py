from fastapi import APIRouter, HTTPException

from .. import warehouse
from ..config import settings
from ..db import fetch_all, fetch_one

router = APIRouter(prefix="/orders", tags=["orders"])

ORDER_SUMMARY = """
SELECT o.*, COUNT(l.id)::INT AS line_count,
       COALESCE(SUM(l.quantity), 0)::INT AS units,
       COALESCE(SUM(l.picked_qty), 0)::INT AS picked_units
FROM orders o LEFT JOIN order_lines l ON l.order_id = o.id
"""


@router.get("")
async def list_orders(status: str | None = None):
    where = "WHERE o.status = %s" if status else ""
    params = (status,) if status else ()
    return await fetch_all(f"{ORDER_SUMMARY} {where} GROUP BY o.id ORDER BY o.priority, o.id", *params)


@router.get("/{order_id}")
async def get_order(order_id: int):
    order = await fetch_one(f"{ORDER_SUMMARY} WHERE o.id = %s GROUP BY o.id", order_id)
    if not order:
        raise HTTPException(404, f"order {order_id} not found")
    order["lines"] = await fetch_all(
        """
        SELECT l.sku, p.name, l.quantity, l.picked_qty
        FROM order_lines l JOIN products p ON p.sku = l.sku
        WHERE l.order_id = %s ORDER BY l.id
        """,
        order_id,
    )
    return order


@router.post("/{order_id}/fulfill")
async def fulfill_order(order_id: int):
    try:
        plan = await warehouse.plan_fulfillment(order_id, settings.robot_ids[0], None, None)
        await warehouse.dispatch_task(plan["task_id"])
    except warehouse.WarehouseError as err:
        await warehouse.raise_exception("planning_failed", str(err), order_id=order_id)
        raise HTTPException(409, str(err)) from err
    return plan

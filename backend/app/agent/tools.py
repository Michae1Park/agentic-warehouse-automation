from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import asyncio
import time

from .. import warehouse
from ..db import fetch_all, fetch_one


@dataclass
class ToolContext:
    conversation_id: str
    robot_id: str


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Callable[..., Awaitable[Any]]

    def schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.parameters,
        }}


def _params(properties: dict | None = None, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties or {}, "required": required or []}


ORDER_ID = {"type": "integer", "description": "Order number, e.g. 1042"}
LOCATION = {"type": "string", "description": "Location id, e.g. A-01-1 or STAGING-1"}
TASK_ID = {"type": "integer", "description": "Task id returned by a planning tool"}


async def list_orders(ctx: ToolContext, status: str | None = None):
    where = "WHERE status = %s" if status else ""
    return await fetch_all(
        f"SELECT id, customer, status, priority, dispatch_location FROM orders {where} ORDER BY priority, id",
        *((status,) if status else ()),
    )


async def get_order(ctx: ToolContext, order_id: int):
    return await warehouse.get_order(order_id) or {"error": f"order {order_id} not found"}


async def find_stock(ctx: ToolContext, sku: str):
    return {"sku": sku, "locations": await warehouse.find_stock(sku)}


async def get_robot_status(ctx: ToolContext):
    return await fetch_one(
        "SELECT id, status, x, y, battery_pct, tote, current_task_id FROM robots WHERE id = %s", ctx.robot_id
    )


async def plan_fulfillment(ctx: ToolContext, order_id: int, dispatch_location: str | None = None):
    return await warehouse.plan_fulfillment(order_id, ctx.robot_id, dispatch_location, ctx.conversation_id)


async def dispatch_task(ctx: ToolContext, task_id: int):
    return await warehouse.dispatch_task(task_id)


async def get_task_status(ctx: ToolContext, task_id: int):
    task = await fetch_one("SELECT id, kind, order_id, status, error, plan FROM tasks WHERE id = %s", task_id)
    if not task:
        return {"error": f"task {task_id} not found"}
    task["steps"] = await fetch_all(
        "SELECT seq, action, args - 'inventory_id' AS args, status, result FROM task_steps "
        "WHERE task_id = %s ORDER BY seq",
        task_id,
    )
    return task


async def wait_for_task(ctx: ToolContext, task_id: int, timeout_s: int = 300):
    deadline = time.monotonic() + min(timeout_s, 600)
    while time.monotonic() < deadline:
        task = await fetch_one("SELECT id, kind, status, error FROM tasks WHERE id = %s", task_id)
        if not task:
            return {"error": f"task {task_id} not found"}
        if task["status"] == "planned":
            return {"error": f"task {task_id} has not been dispatched; call dispatch_task first"}
        if task["status"] in ("succeeded", "failed", "cancelled"):
            return task
        await asyncio.sleep(0.5)
    return {"id": task_id, "status": "still_running", "note": "timed out waiting"}


async def cancel_task(ctx: ToolContext, task_id: int):
    return await warehouse.cancel_task(task_id)


async def get_location(ctx: ToolContext, location_id: str):
    location = await fetch_one("SELECT id, kind, aisle, bay, level FROM locations WHERE id = %s", location_id)
    if not location:
        return {"error": f"location {location_id} not found"}
    location["stock"] = await fetch_all(
        "SELECT sku, quantity, reserved FROM inventory WHERE location_id = %s AND quantity > 0", location_id
    )
    return location


async def find_empty_bins(ctx: ToolContext, aisle: str | None = None):
    return await fetch_all(
        """
        SELECT l.id FROM locations l
        WHERE l.kind = 'storage' AND (%s::text IS NULL OR l.aisle = %s)
          AND NOT EXISTS (SELECT 1 FROM inventory i WHERE i.location_id = l.id AND i.quantity > 0)
        ORDER BY l.id
        """,
        aisle, aisle,
    )


async def inspect_inventory(ctx: ToolContext, location_ids: list[str] | None = None, aisle: str | None = None):
    if not location_ids:
        rows = await fetch_all(
            "SELECT id FROM locations WHERE kind = 'storage' AND (%s::text IS NULL OR aisle = %s) ORDER BY id",
            aisle, aisle,
        )
        location_ids = [r["id"] for r in rows]
    return await warehouse.plan_inspection(location_ids, ctx.robot_id, ctx.conversation_id)


async def relocate_stock(ctx: ToolContext, sku: str, from_location: str, to_location: str, quantity: int):
    return await warehouse.plan_relocation(sku, from_location, to_location, quantity, ctx.robot_id, ctx.conversation_id)


async def replenish_stock(ctx: ToolContext, sku: str, to_location: str, quantity: int, from_location: str = "STAGING-1"):
    return await warehouse.plan_relocation(
        sku, from_location, to_location, quantity, ctx.robot_id, ctx.conversation_id, kind="replenishment"
    )


async def list_exceptions(ctx: ToolContext, status: str | None = "open"):
    where = "WHERE status = %s" if status else ""
    return await fetch_all(
        f"SELECT id, type, severity, message, status, order_id, task_id FROM exceptions {where} ORDER BY id DESC",
        *((status,) if status else ()),
    )


async def report_exception(ctx: ToolContext, type: str, message: str, order_id: int | None = None):
    return await warehouse.raise_exception(type, message, "warning", order_id=order_id)


TOOLS = [
    Tool("list_orders", "List orders, optionally filtered by status.",
         _params({"status": {"type": "string", "enum": ["pending", "in_progress", "completed", "failed", "cancelled"]}}),
         list_orders),
    Tool("get_order", "Get an order with its line items (SKU, product name, quantity, picked quantity).",
         _params({"order_id": ORDER_ID}, ["order_id"]), get_order),
    Tool("find_stock", "Find shelf locations and available quantity for a SKU.",
         _params({"sku": {"type": "string"}}, ["sku"]), find_stock),
    Tool("get_robot_status", "Get the picking robot's status, position, battery and tote contents.",
         _params(), get_robot_status),
    Tool("plan_fulfillment",
         "Reserve inventory for a pending order and create a pick task with an optimized route. "
         "Fails with a reason if stock is insufficient. Does not move the robot.",
         _params({"order_id": ORDER_ID, "dispatch_location": {
             "type": "string", "description": "Dispatch area, e.g. DISPATCH-1. Defaults to the order's assigned area."}},
             ["order_id"]),
         plan_fulfillment),
    Tool("get_location", "Get a location's contents.",
         _params({"location_id": LOCATION}, ["location_id"]), get_location),
    Tool("find_empty_bins", "List empty storage bins, optionally within one aisle (A, B or C).",
         _params({"aisle": {"type": "string"}}), find_empty_bins),
    Tool("inspect_inventory",
         "Plan a cycle-count task: the robot visits locations, counts stock visually and corrects the system of record. "
         "Give explicit location_ids or an aisle; with neither, all storage bins are counted.",
         _params({"location_ids": {"type": "array", "items": {"type": "string"}}, "aisle": {"type": "string"}}),
         inspect_inventory),
    Tool("relocate_stock", "Plan a task moving units of a SKU from one storage location to another.",
         _params({"sku": {"type": "string"}, "from_location": LOCATION, "to_location": LOCATION,
                  "quantity": {"type": "integer"}}, ["sku", "from_location", "to_location", "quantity"]),
         relocate_stock),
    Tool("replenish_stock",
         "Plan a replenishment task moving received stock from the staging/receiving area to a storage bin.",
         _params({"sku": {"type": "string"}, "to_location": LOCATION, "quantity": {"type": "integer"},
                  "from_location": {"type": "string", "description": "Defaults to STAGING-1 (receiving)"}},
                 ["sku", "to_location", "quantity"]),
         replenish_stock),
    Tool("dispatch_task", "Send a planned task to the robot for execution. Returns immediately; the robot works asynchronously.",
         _params({"task_id": TASK_ID}, ["task_id"]), dispatch_task),
    Tool("get_task_status", "Get a task's status and per-step progress.",
         _params({"task_id": TASK_ID}, ["task_id"]), get_task_status),
    Tool("wait_for_task",
         "Block until a dispatched task finishes, then return its final status. Use only when a following step "
         "depends on that task's outcome (e.g. fulfill after replenishment).",
         _params({"task_id": TASK_ID, "timeout_s": {"type": "integer"}}, ["task_id"]), wait_for_task),
    Tool("cancel_task", "Cancel a planned or queued task and release its inventory reservations.",
         _params({"task_id": TASK_ID}, ["task_id"]), cancel_task),
    Tool("list_exceptions", "List operational exceptions.",
         _params({"status": {"type": "string", "enum": ["open", "resolved", "escalated"]}}), list_exceptions),
    Tool("report_exception", "Record an operational exception that needs human attention.",
         _params({"type": {"type": "string", "description": "Short code, e.g. stock_shortage"},
                  "message": {"type": "string"}, "order_id": ORDER_ID}, ["type", "message"]),
         report_exception),
]

TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}

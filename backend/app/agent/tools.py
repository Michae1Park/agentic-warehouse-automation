from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

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
TASK_ID = {"type": "integer", "description": "Task id returned by plan_fulfillment"}


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
    task = await fetch_one("SELECT id, order_id, status, error, plan FROM tasks WHERE id = %s", task_id)
    if not task:
        return {"error": f"task {task_id} not found"}
    task["steps"] = await fetch_all(
        "SELECT seq, action, args - 'inventory_id' AS args, status, result FROM task_steps "
        "WHERE task_id = %s ORDER BY seq",
        task_id,
    )
    return task


async def cancel_task(ctx: ToolContext, task_id: int):
    return await warehouse.cancel_task(task_id)


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
    Tool("dispatch_task", "Send a planned task to the robot for execution. Returns immediately; the robot works asynchronously.",
         _params({"task_id": TASK_ID}, ["task_id"]), dispatch_task),
    Tool("get_task_status", "Get a task's status and per-step progress.",
         _params({"task_id": TASK_ID}, ["task_id"]), get_task_status),
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

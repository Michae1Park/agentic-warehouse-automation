import asyncio
import json
import logging
import time
from collections import defaultdict

from openai import AsyncOpenAI
from psycopg.types.json import Jsonb

from ..config import settings
from ..db import fetch_all, fetch_one
from ..warehouse import WarehouseError
from .tools import TOOLS, TOOLS_BY_NAME, ToolContext

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the operations agent for an automated warehouse. You help the warehouse manager \
by querying the warehouse management system and directing a mobile picking robot.

How to work:
- Always ground answers in tool results. Never invent orders, SKUs, quantities or locations.
- To fulfill an order: get_order, then plan_fulfillment, then dispatch_task. Inventory is reserved at planning \
and decremented automatically as the robot picks.
- Other workflows: inspect_inventory (cycle count), relocate_stock, replenish_stock (from receiving). Every \
planning tool creates a task that must then be sent with dispatch_task. Tasks queue and run in order.
- Never poll get_task_status in a loop. If a later step depends on a task's outcome, call wait_for_task.
- For a stock shortage, check whether the SKU is waiting in receiving (get_location STAGING-1). If receiving \
covers the gap, recover without asking by cross-docking: plan_fulfillment again with include_receiving=true and \
dispatch_task it, so the robot picks the shelf stock and the received units in one trip. In the same turn, right \
after that dispatch, put away the surplus: receiving quantity minus the units the order takes from receiving, via \
replenish_stock into a bin that already holds the SKU, then dispatch_task it so it queues behind the order. Never \
restock units that the order needs and then pick them back out.
- If planning fails for a reason receiving cannot cover, do not dispatch. Explain the problem, report_exception, \
and suggest options such as partial fulfillment or restocking.
- Dispatching is asynchronous. After dispatch, tell the manager the task id, the pick route, and that you will \
report back when it finishes.
- Messages starting with [SYSTEM EVENT] are notifications from the robot executor. Summarize the outcome for \
the manager; on failure, inspect the task and exceptions and recommend a next step.
- If a task is paused because the target is blocked by another item at the shelf, call clear_blocker on that \
task and then dispatch_task to resume it; tell the manager what is in the way and that the robot will push it aside.
- After a cycle count, report every discrepancy from the findings. Misplaced stock must go back to its home bin: \
whenever a SKU was found in a bin where it was not recorded and the same count found that SKU short at another bin, \
immediately call relocate_stock (from the bin where it was found to the short bin, the found quantity) and then \
dispatch_task, without asking. Recording it at the wrong bin is not a fix.
- Reply in at most 3 short bullet lines, under 12 words each. No paragraphs, no headings, no repeating \
tool output. Use order numbers, SKUs and location ids. For discrepancies, one bullet per bin."""

client = AsyncOpenAI(base_url=settings.llm_base_url, api_key="unused")
_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def _dump(value) -> str:
    return json.dumps(value, default=str)


async def _history(conversation_id: str) -> list[dict]:
    rows = await fetch_all(
        "SELECT message FROM agent_messages WHERE conversation_id = %s ORDER BY id", conversation_id
    )
    return [r["message"] for r in rows]


async def _append(conversation_id: str, message: dict) -> None:
    await fetch_one(
        "INSERT INTO agent_messages (conversation_id, message) VALUES (%s, %s) RETURNING id",
        conversation_id, Jsonb(message),
    )


async def _call_tool(ctx: ToolContext, name: str, raw_args: str) -> dict:
    tool = TOOLS_BY_NAME.get(name)
    if not tool:
        return {"error": f"unknown tool {name}"}
    try:
        args = json.loads(raw_args or "{}")
        return {"result": await tool.handler(ctx, **args)}
    except WarehouseError as err:
        return {"error": str(err)}
    except (json.JSONDecodeError, TypeError) as err:
        return {"error": f"invalid arguments: {err}"}


async def run_agent(conversation_id: str, user_message: str, robot_id: str | None = None) -> dict:
    async with _locks[conversation_id]:
        return await _run(conversation_id, user_message, robot_id or settings.robot_ids[0])


async def _run(conversation_id: str, user_message: str, robot_id: str) -> dict:
    ctx = ToolContext(conversation_id, robot_id)
    run = await fetch_one(
        "INSERT INTO agent_runs (conversation_id, user_message) VALUES (%s, %s) RETURNING id",
        conversation_id, user_message,
    )
    await _append(conversation_id, {"role": "user", "content": user_message})
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, *await _history(conversation_id)]
    stats = {"llm_calls": 0, "tool_calls": 0, "llm_ms": 0, "tool_ms": 0, "prompt_tokens": 0, "completion_tokens": 0}
    reply = "I could not complete this request within the step limit."

    for _ in range(settings.agent_max_iterations):
        started = time.perf_counter()
        response = await client.chat.completions.create(
            model=settings.llm_model,
            messages=messages,
            tools=[tool.schema() for tool in TOOLS],
            temperature=0.2,
            extra_body={"chat_template_kwargs": {"enable_thinking": settings.llm_thinking}},
        )
        stats["llm_ms"] += int((time.perf_counter() - started) * 1000)
        stats["llm_calls"] += 1
        if response.usage:
            stats["prompt_tokens"] += response.usage.prompt_tokens
            stats["completion_tokens"] += response.usage.completion_tokens

        choice = response.choices[0].message
        assistant = {"role": "assistant", "content": choice.content or ""}
        if choice.tool_calls:
            assistant["tool_calls"] = [
                {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
                for c in choice.tool_calls
            ]
        messages.append(assistant)
        await _append(conversation_id, assistant)

        if not choice.tool_calls:
            reply = assistant["content"]
            break

        for call in choice.tool_calls:
            started = time.perf_counter()
            outcome = await _call_tool(ctx, call.function.name, call.function.arguments)
            stats["tool_ms"] += int((time.perf_counter() - started) * 1000)
            stats["tool_calls"] += 1
            tool_message = {"role": "tool", "tool_call_id": call.id, "name": call.function.name, "content": _dump(outcome)}
            messages.append(tool_message)
            await _append(conversation_id, tool_message)

    await fetch_one(
        """
        UPDATE agent_runs SET reply = %s, llm_calls = %s, tool_calls = %s, llm_ms = %s, tool_ms = %s,
               prompt_tokens = %s, completion_tokens = %s, finished_at = now()
        WHERE id = %s RETURNING id
        """,
        reply, stats["llm_calls"], stats["tool_calls"], stats["llm_ms"], stats["tool_ms"],
        stats["prompt_tokens"], stats["completion_tokens"], run["id"],
    )
    return {"reply": reply, "run_id": run["id"], **stats}


async def notify_task_finished(task: dict) -> None:
    if not task.get("conversation_id"):
        return
    if task["status"] == "failed":
        detail = f"failed with {task['error']}"
    elif task["status"] == "planned" and task.get("error"):
        detail = f"paused: {task['error']}"
    else:
        detail = task["status"]
    subject = f"for order {task['order_id']}" if task["order_id"] else f"({task['instruction']})"
    findings = await fetch_all(
        "SELECT message FROM exceptions WHERE task_id = %s AND type IN ('inventory_discrepancy', 'unexpected_item') ORDER BY id",
        task["id"],
    )
    report = "".join(f"\n- {row['message']}" for row in findings)
    await run_agent(
        task["conversation_id"],
        f"[SYSTEM EVENT] {task['kind'].capitalize()} task {task['id']} {subject} {detail}."
        + (f" Findings:{report}" if report else ""),
        task["robot_id"],
    )

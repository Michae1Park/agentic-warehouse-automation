import asyncio
import json
import logging
import time
from collections import defaultdict
from pathlib import Path

from openai import AsyncOpenAI
from psycopg.types.json import Jsonb

from ..config import settings
from ..db import fetch_all, fetch_one
from ..warehouse import WarehouseError
from . import harness
from .tools import TOOLS_BY_NAME, ToolContext, active_tools

log = logging.getLogger(__name__)

PROMPTS = Path(__file__).parent / "prompts"
GENERAL_RULES = (PROMPTS / "general_rules.md").read_text().strip()
RECIPES = (PROMPTS / "recipes.md").read_text().strip()
HARNESS_PLAN = (PROMPTS / "harness_plan.md").read_text().strip()


def system_prompt() -> str:
    parts = [GENERAL_RULES]
    if harness.on(harness.PLAN):
        parts.append(HARNESS_PLAN)
    if settings.agent_recipes:
        parts.append(RECIPES)
    return "\n\n".join(parts)


client = AsyncOpenAI(base_url=settings.llm_base_url, api_key="unused")
_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def _dump(value) -> str:
    return json.dumps(value, default=str)


async def _append(conversation_id: str, message: dict) -> int:
    row = await fetch_one(
        "INSERT INTO agent_messages (conversation_id, message) VALUES (%s, %s) RETURNING id",
        conversation_id, Jsonb(message),
    )
    return row["id"]


async def _call_tool(ctx: ToolContext, name: str, raw_args: str) -> dict:
    tool = TOOLS_BY_NAME.get(name)
    if not tool or tool not in active_tools():
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
    history, history_ids = await harness.history(conversation_id)
    messages, ids = [{"role": "system", "content": system_prompt()}, *history], [None, *history_ids]
    stats = {"llm_calls": 0, "tool_calls": 0, "llm_ms": 0, "tool_ms": 0, "prompt_tokens": 0, "completion_tokens": 0,
             "peak_prompt_tokens": 0}
    reply = "I could not complete this request within the step limit."
    last_prompt, added_chars = 0, 0

    async def add(message: dict) -> None:
        nonlocal added_chars
        messages.append(message)
        ids.append(await _append(conversation_id, message))
        added_chars += len(str(message.get("content") or "")) + len(json.dumps(message.get("tool_calls") or []))

    event = await harness.on_event(conversation_id, user_message)
    if event:
        await add({"role": "user", "content": event})

    for _ in range(settings.agent_max_iterations):
        if harness.needs_compaction(last_prompt, added_chars):
            messages, ids = await harness.compact(client, conversation_id, messages, ids)
            last_prompt, added_chars = 0, sum(len(str(m.get("content") or "")) for m in messages)
        request = messages
        if harness.on(harness.PLAN):
            request = [*messages, {"role": "user", "content": await harness.plan_message(conversation_id)}]
        started = time.perf_counter()
        response = await client.chat.completions.create(
            model=settings.llm_model,
            messages=request,
            tools=[tool.schema() for tool in active_tools()],
            temperature=settings.agent_temperature,
            extra_body={"chat_template_kwargs": {"enable_thinking": settings.llm_thinking}},
        )
        stats["llm_ms"] += int((time.perf_counter() - started) * 1000)
        stats["llm_calls"] += 1
        if response.usage:
            stats["prompt_tokens"] += response.usage.prompt_tokens
            stats["completion_tokens"] += response.usage.completion_tokens
            stats["peak_prompt_tokens"] = max(stats["peak_prompt_tokens"], response.usage.prompt_tokens)
            last_prompt, added_chars = response.usage.prompt_tokens, 0

        choice = response.choices[0].message
        assistant = {"role": "assistant", "content": choice.content or ""}
        if choice.tool_calls:
            assistant["tool_calls"] = [
                {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
                for c in choice.tool_calls
            ]
        await add(assistant)

        if not choice.tool_calls:
            reply = assistant["content"]
            review = await harness.final_review(conversation_id)
            if not review:
                break
            await add({"role": "user", "content": review})
            continue

        injections = []
        for call in choice.tool_calls:
            started = time.perf_counter()
            refusal = await harness.gate(conversation_id, call.function.name)
            if refusal:
                outcome = {"error": refusal}
            else:
                outcome = await _call_tool(ctx, call.function.name, call.function.arguments)
                injection = await harness.after_tool(conversation_id, call.function.name, call.function.arguments, outcome)
                if injection:
                    injections.append(injection)
            stats["tool_ms"] += int((time.perf_counter() - started) * 1000)
            stats["tool_calls"] += 1
            await add({"role": "tool", "tool_call_id": call.id, "name": call.function.name, "content": _dump(outcome)})
        for injection in injections[-1:]:
            await add({"role": "user", "content": injection})

    await fetch_one(
        """
        UPDATE agent_runs SET reply = %s, llm_calls = %s, tool_calls = %s, llm_ms = %s, tool_ms = %s,
               prompt_tokens = %s, completion_tokens = %s, peak_prompt_tokens = %s, finished_at = now()
        WHERE id = %s RETURNING id
        """,
        reply, stats["llm_calls"], stats["tool_calls"], stats["llm_ms"], stats["tool_ms"],
        stats["prompt_tokens"], stats["completion_tokens"], stats["peak_prompt_tokens"], run["id"],
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

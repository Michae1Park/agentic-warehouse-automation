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
from .tools import TOOLS, TOOLS_BY_NAME, ToolContext

log = logging.getLogger(__name__)

PROMPTS = Path(__file__).parent / "prompts"
GENERAL_RULES = (PROMPTS / "general_rules.md").read_text().strip()
RECIPES = (PROMPTS / "recipes.md").read_text().strip()


def system_prompt() -> str:
    return f"{GENERAL_RULES}\n\n{RECIPES}" if settings.agent_recipes else GENERAL_RULES


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
    messages = [{"role": "system", "content": system_prompt()}, *await _history(conversation_id)]
    stats = {"llm_calls": 0, "tool_calls": 0, "llm_ms": 0, "tool_ms": 0, "prompt_tokens": 0, "completion_tokens": 0}
    reply = "I could not complete this request within the step limit."

    for _ in range(settings.agent_max_iterations):
        started = time.perf_counter()
        response = await client.chat.completions.create(
            model=settings.llm_model,
            messages=messages,
            tools=[tool.schema() for tool in TOOLS],
            temperature=settings.agent_temperature,
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

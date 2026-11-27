import json
import re

from .. import warehouse
from ..config import settings
from ..db import fetch_all, fetch_one
from ..warehouse import WarehouseError

PLAN, ANCHOR, CHECK, REPLAN = "plan", "anchor", "check", "replan"
CHECK_TYPES = ("order_dispatched", "order_flagged", "order_untouched", "task_not_paused", "stock_put_away")
STATUSES = ("open", "in_progress", "done", "failed", "dropped")
UNFINISHED = ("open", "in_progress", "failed")
ACTIVE_TASK = ("planned", "queued", "running")
ROBOT_TOOLS = {"plan_fulfillment", "dispatch_task", "reallocate_pick", "clear_blocker",
               "relocate_stock", "replenish_stock", "inspect_inventory"}
STOCK_TOOLS = {"plan_fulfillment", "reallocate_pick"}
RECEIVING = "STAGING-1"
UNRESOLVED = "unresolved_subgoal"
SKU_PATTERN = re.compile(r"SKU-\d+")


def on(component: str) -> bool:
    return component in settings.agent_harness


async def log_event(conversation_id: str, kind: str, detail: str = "") -> None:
    await fetch_one(
        "INSERT INTO agent_harness_events (conversation_id, kind, detail) VALUES (%s, %s, %s) RETURNING id",
        conversation_id, kind, detail[:2000],
    )


async def active_plan(conversation_id: str) -> dict | None:
    return await fetch_one(
        "SELECT * FROM agent_plans WHERE conversation_id = %s AND active ORDER BY id DESC LIMIT 1", conversation_id
    )


async def plan_subgoals(plan_id: int) -> list[dict]:
    return await fetch_all("SELECT * FROM agent_subgoals WHERE plan_id = %s ORDER BY id", plan_id)


def _validate(items: list[dict], existing: set[str]) -> list[dict]:
    keys = set(existing)
    parsed = []
    for item in items:
        key = str(item.get("id") or "").strip()
        check = item.get("check")
        if not key or key in keys:
            raise WarehouseError(f"subgoal id '{key}' is missing or duplicated")
        if check not in CHECK_TYPES:
            raise WarehouseError(f"subgoal {key}: check must be one of {', '.join(CHECK_TYPES)}")
        if check.startswith("order_") and not item.get("order_id"):
            raise WarehouseError(f"subgoal {key}: {check} needs order_id")
        if check == "task_not_paused" and not item.get("task_id"):
            raise WarehouseError(f"subgoal {key}: task_not_paused needs task_id")
        if check == "stock_put_away" and not item.get("sku"):
            raise WarehouseError(f"subgoal {key}: stock_put_away needs sku")
        keys.add(key)
        parsed.append({**item, "id": key, "depends_on": [str(d) for d in item.get("depends_on") or []]})
    graph = {item["id"]: item["depends_on"] for item in parsed}
    for item in parsed:
        unknown = [d for d in item["depends_on"] if d not in keys]
        if unknown:
            raise WarehouseError(f"subgoal {item['id']}: unknown dependency {unknown[0]}")
    visiting, visited = set(), set()

    def visit(key: str) -> None:
        if key in visited or key not in graph:
            return
        if key in visiting:
            raise WarehouseError(f"dependency cycle through subgoal {key}")
        visiting.add(key)
        for dep in graph[key]:
            visit(dep)
        visiting.discard(key)
        visited.add(key)

    for key in graph:
        visit(key)
    return parsed


async def _insert(plan_id: int, items: list[dict]) -> None:
    for item in items:
        await fetch_one(
            """INSERT INTO agent_subgoals (plan_id, key, title, check_type, order_id, task_id, sku, depends_on)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            plan_id, item["id"], item.get("title") or item["id"], item["check"], item.get("order_id"),
            item.get("task_id"), item.get("sku"), item["depends_on"],
        )


async def _progressed(plan_id: int) -> None:
    await fetch_one("UPDATE agent_plans SET stall_calls = 0, needs_revision = NULL WHERE id = %s RETURNING id", plan_id)


async def create_plan(ctx, objective: str, subgoals: list[dict]):
    items = _validate(subgoals, set())
    previous = await active_plan(ctx.conversation_id)
    if previous:
        await fetch_one("UPDATE agent_plans SET active = false WHERE id = %s RETURNING id", previous["id"])
        await log_event(ctx.conversation_id, "revision", "create_plan replaced the active plan")
    plan = await fetch_one(
        "INSERT INTO agent_plans (conversation_id, objective, reviewed) VALUES (%s, %s, %s) RETURNING id",
        ctx.conversation_id, objective, bool(previous and previous["reviewed"]),
    )
    await _insert(plan["id"], items)
    return {"plan_id": plan["id"], "subgoals": len(items)}


async def add_subgoals(ctx, subgoals: list[dict]):
    plan = await _require_plan(ctx.conversation_id)
    existing = {s["key"] for s in await plan_subgoals(plan["id"])}
    items = _validate(subgoals, existing)
    await _insert(plan["id"], items)
    await _progressed(plan["id"])
    await log_event(ctx.conversation_id, "revision", f"added {', '.join(i['id'] for i in items)}")
    return {"added": [i["id"] for i in items]}


async def update_subgoal(ctx, id: str, status: str, note: str | None = None, check: str | None = None,
                         order_id: int | None = None, task_id: int | None = None):
    plan = await _require_plan(ctx.conversation_id)
    subgoal = await fetch_one("SELECT * FROM agent_subgoals WHERE plan_id = %s AND key = %s", plan["id"], id)
    if not subgoal:
        raise WarehouseError(f"no subgoal {id} in the active plan")
    if status not in STATUSES:
        raise WarehouseError(f"status must be one of {', '.join(STATUSES)}")
    if check and check not in CHECK_TYPES:
        raise WarehouseError(f"check must be one of {', '.join(CHECK_TYPES)}")
    revised = {**subgoal, "check_type": check or subgoal["check_type"], "order_id": order_id or subgoal["order_id"],
               "task_id": task_id or subgoal["task_id"]}
    reply = {"id": id, "status": status}
    if status == "done" and on(CHECK):
        state, reason = await verify(revised)
        if state != "ok":
            reply = {"id": id, "status": subgoal["status"], "check": state, "reason": reason}
            if state == "fail" and on(REPLAN):
                reply["replan"] = await trigger(ctx.conversation_id, f"check failed for {id}: {reason}",
                                                order_ids=[revised["order_id"]] if revised["order_id"] else [])
            return reply
    await fetch_one(
        """UPDATE agent_subgoals SET status = %s, note = COALESCE(%s, note), check_type = %s, order_id = %s,
           task_id = %s, updated_at = now() WHERE id = %s RETURNING id""",
        status, note, revised["check_type"], revised["order_id"], revised["task_id"], subgoal["id"],
    )
    if check or status in ("dropped", "failed"):
        await log_event(ctx.conversation_id, "revision", f"{id} -> {status} {check or ''}".strip())
    await _progressed(plan["id"])
    return reply


async def get_plan(ctx):
    plan = await _require_plan(ctx.conversation_id)
    return {"objective": plan["objective"], "table": render_table(await plan_subgoals(plan["id"]))}


async def _require_plan(conversation_id: str) -> dict:
    plan = await active_plan(conversation_id)
    if not plan:
        raise WarehouseError("no active plan; call create_plan first")
    return plan


async def _order_tasks(order_id: int) -> list[dict]:
    return await fetch_all("SELECT id, status, error FROM tasks WHERE order_id = %s ORDER BY id", order_id)


async def verify(subgoal: dict) -> tuple[str, str]:
    check = subgoal["check_type"]
    if check.startswith("order_"):
        order = await fetch_one("SELECT status FROM orders WHERE id = %s", subgoal["order_id"])
        if not order:
            return "fail", f"order {subgoal['order_id']} does not exist"
        tasks = await _order_tasks(subgoal["order_id"])
        active = [t for t in tasks if t["status"] in ACTIVE_TASK]
        if check == "order_dispatched":
            if order["status"] == "completed":
                return "ok", "order completed"
            for task in active:
                if task["status"] == "planned":
                    return "fail", f"task {task['id']} is planned but " + (
                        f"paused: {task['error']}" if task["error"] else "not dispatched")
                return "pending", f"task {task['id']} is {task['status']}"
            return "fail", f"order is {order['status']} and has no active task"
        if check == "order_flagged":
            flag = await fetch_one(
                "SELECT id FROM exceptions WHERE order_id = %s AND type <> %s LIMIT 1", subgoal["order_id"], UNRESOLVED)
            if active:
                return "fail", f"order still has active task {active[0]['id']}"
            return ("ok", "exception recorded") if flag else ("fail", "no exception recorded for this order")
        if order["status"] == "pending" and not tasks:
            return "ok", "order untouched"
        return "fail", f"order is {order['status']} with {len(tasks)} task(s)"
    if check == "task_not_paused":
        task = await fetch_one("SELECT status, error FROM tasks WHERE id = %s", subgoal["task_id"])
        if not task:
            return "fail", f"task {subgoal['task_id']} does not exist"
        if task["status"] in ("queued", "running"):
            return "pending", f"task is {task['status']}"
        if task["status"] == "planned":
            return "fail", f"task is planned{': ' + task['error'] if task['error'] else ' and not dispatched'}"
        return "ok", f"task {task['status']}"
    row = await fetch_one(
        "SELECT COALESCE(SUM(quantity), 0)::INT AS quantity FROM inventory WHERE location_id = %s AND sku = %s",
        RECEIVING, subgoal["sku"],
    )
    if row["quantity"] == 0:
        return "ok", f"no {subgoal['sku']} left in receiving"
    moving = await fetch_one(
        """SELECT t.id FROM tasks t JOIN task_steps s ON s.task_id = t.id
           WHERE t.kind = 'replenishment' AND t.status IN ('queued', 'running') AND s.args->>'sku' = %s LIMIT 1""",
        subgoal["sku"],
    )
    if moving:
        return "pending", f"put-away task {moving['id']} is running"
    return "fail", f"{row['quantity']} unit(s) of {subgoal['sku']} still in receiving"


def render_table(rows: list[dict]) -> str:
    lines = ["id | status | check | target | depends_on | note"]
    for s in rows:
        target = s["order_id"] or s["task_id"] or s["sku"] or ""
        lines.append(f"{s['key']} | {s['status']} | {s['check_type']} | {target} | "
                     f"{','.join(s['depends_on']) or '-'} | {s['note'] or ''}")
    return "\n".join(lines)


async def plan_message(conversation_id: str) -> str:
    plan = await active_plan(conversation_id)
    if not plan:
        return "[HARNESS PLAN] No plan yet. Call create_plan before any robot action."
    text = f"[HARNESS PLAN] Objective: {plan['objective']}\n{render_table(await plan_subgoals(plan['id']))}"
    if plan["needs_revision"]:
        text += f"\nRevision required before the next robot action: {plan['needs_revision']}"
    return text


async def gate(conversation_id: str, tool_name: str) -> str | None:
    if tool_name not in ROBOT_TOOLS or not on(PLAN):
        return None
    plan = await active_plan(conversation_id)
    if not plan:
        return "no plan yet: call create_plan (one subgoal per order or chore) before robot actions"
    if on(REPLAN) and plan["needs_revision"]:
        return (f"plan revision required first ({plan['needs_revision']}): call update_subgoal, add_subgoals "
                "or create_plan")
    return None


async def _priority_conflicts(order_ids: list[int], skus: set[str]) -> list[str]:
    needs = await fetch_all(
        """SELECT o.id, o.priority, l.sku, l.quantity FROM orders o JOIN order_lines l ON l.order_id = o.id
           WHERE o.status = 'pending' AND NOT EXISTS (
               SELECT 1 FROM tasks t WHERE t.order_id = o.id AND t.status IN ('planned', 'queued', 'running'))
             AND NOT EXISTS (SELECT 1 FROM order_lines m JOIN products p ON p.sku = m.sku
                             WHERE m.order_id = o.id AND NOT p.robot_pickable)
             AND (o.id = ANY(%s) OR l.sku = ANY(%s))""",
        order_ids, list(skus),
    )
    holders = await fetch_all(
        """SELECT DISTINCT o.id, o.priority, l.sku, t.id AS task_id, t.status FROM orders o
           JOIN order_lines l ON l.order_id = o.id JOIN tasks t ON t.order_id = o.id
           WHERE t.status IN ('planned', 'queued') AND l.sku = ANY(%s)""",
        list(skus | {n["sku"] for n in needs}),
    )
    available = {r["sku"]: r["available"] for r in await fetch_all("SELECT sku, available FROM inventory_levels")}
    conflicts = []
    for need in needs:
        if available.get(need["sku"], 0) >= need["quantity"]:
            continue
        for holder in holders:
            if holder["sku"] == need["sku"] and holder["priority"] > need["priority"]:
                conflicts.append(
                    f"#{holder['id']} (P{holder['priority']}, task {holder['task_id']} {holder['status']}) holds "
                    f"{need['sku']} that #{need['id']} (P{need['priority']}) needs; cancel_task releases it")
    return sorted(set(conflicts))


async def trigger(conversation_id: str, reason: str, text: str = "", order_ids: list[int] | None = None) -> str:
    order_ids = [o for o in order_ids or [] if o]
    skus = set(SKU_PATTERN.findall(f"{reason} {text}"))
    for order_id in order_ids:
        skus |= {r["sku"] for r in await fetch_all("SELECT sku FROM order_lines WHERE order_id = %s", order_id)}
    stock = await fetch_all("SELECT sku, available FROM inventory_levels WHERE sku = ANY(%s) ORDER BY sku", list(skus))
    plan = await active_plan(conversation_id)
    affected = []
    if plan:
        sku_orders = {r["order_id"] for r in await fetch_all(
            "SELECT DISTINCT order_id FROM order_lines WHERE sku = ANY(%s)", list(skus))}
        for s in await plan_subgoals(plan["id"]):
            if s["status"] in UNFINISHED and (s["order_id"] in sku_orders or s["order_id"] in order_ids
                                              or s["sku"] in skus):
                affected.append(f"{s['key']} ({s['status']}, {s['check_type']} {s['order_id'] or s['sku'] or ''})")
    conflicts = await _priority_conflicts(order_ids, skus)
    lines = [f"[HARNESS REPLAN] Trigger: {reason}"]
    if stock:
        lines.append("Available now: " + ", ".join(f"{r['sku']} {r['available']}" for r in stock))
    lines.append("Affected subgoals: " + (", ".join(affected) or "none in the plan"))
    lines.append("Priority conflicts: " + ("; ".join(conflicts) or "none"))
    if plan:
        lines.append("Revise the remaining plan (update_subgoal / add_subgoals / create_plan) before the next robot "
                     "action. An order that can no longer be filled should switch to check order_flagged.")
        await fetch_one("UPDATE agent_plans SET needs_revision = %s WHERE id = %s RETURNING id", reason[:300], plan["id"])
    await log_event(conversation_id, "replan", reason)
    return "\n".join(lines)


async def after_tool(conversation_id: str, name: str, args: str, outcome: dict) -> str | None:
    if name in PLAN_TOOL_NAMES:
        return None
    if on(REPLAN) and name in STOCK_TOOLS and "error" in outcome and "robot-pickable" not in outcome["error"]:
        try:
            parsed = json.loads(args or "{}")
        except json.JSONDecodeError:
            parsed = {}
        order_ids = [parsed.get("order_id")]
        if parsed.get("task_id"):
            task = await fetch_one("SELECT order_id FROM tasks WHERE id = %s", parsed["task_id"])
            order_ids.append(task and task["order_id"])
        return await trigger(conversation_id, f"{name} failed: {outcome['error']}", order_ids=order_ids)
    plan = await active_plan(conversation_id)
    if not (on(REPLAN) and on(PLAN) and plan) or plan["needs_revision"]:
        return None
    unfinished = [s for s in await plan_subgoals(plan["id"]) if s["status"] in UNFINISHED]
    if not unfinished:
        return None
    row = await fetch_one(
        "UPDATE agent_plans SET stall_calls = stall_calls + 1 WHERE id = %s RETURNING stall_calls", plan["id"])
    if row["stall_calls"] < settings.stall_tool_calls:
        return None
    return await trigger(conversation_id, f"{row['stall_calls']} tool calls without subgoal progress")


async def on_event(conversation_id: str, message: str) -> str | None:
    if not (on(REPLAN) and message.startswith("[SYSTEM EVENT]")):
        return None
    if "item_not_found" not in message and "Findings" not in message and "failed" not in message:
        return None
    task_id = re.search(r"task (\d+)", message)
    order_ids = []
    if task_id:
        task = await fetch_one("SELECT order_id FROM tasks WHERE id = %s", int(task_id.group(1)))
        order_ids = [task and task["order_id"]]
    return await trigger(conversation_id, "stock or task changed: " + message.removeprefix("[SYSTEM EVENT] ")[:300],
                         order_ids=order_ids)


async def _in_flight(conversation_id: str) -> bool:
    running = await fetch_one(
        "SELECT id FROM tasks WHERE conversation_id = %s AND status = 'running' LIMIT 1", conversation_id)
    queued = await fetch_one(
        "SELECT id FROM tasks WHERE conversation_id = %s AND status = 'queued' LIMIT 1", conversation_id)
    paused = await fetch_one(
        "SELECT id FROM tasks WHERE conversation_id = %s AND status = 'planned' AND error IS NOT NULL LIMIT 1",
        conversation_id)
    return bool(running or (queued and not paused))


async def _stalled(conversation_id: str) -> tuple[dict | None, list[tuple[dict, str]]]:
    plan = await active_plan(conversation_id) if on(PLAN) else None
    if plan:
        stalled = []
        for s in await plan_subgoals(plan["id"]):
            if s["status"] in ("done", "dropped"):
                continue
            state, reason = await verify(s)
            if state == "ok":
                await fetch_one("UPDATE agent_subgoals SET status = 'done', updated_at = now() WHERE id = %s RETURNING id",
                                s["id"])
            elif state == "fail":
                stalled.append((s, reason))
        return plan, stalled
    paused = await fetch_all(
        "SELECT id, order_id, error FROM tasks WHERE conversation_id = %s AND status = 'planned' ORDER BY id",
        conversation_id)
    return None, [({"key": f"task {t['id']}", "title": f"task {t['id']}", "order_id": t["order_id"]},
                   f"task {t['id']} is planned " + (f"and paused: {t['error']}" if t["error"] else "but not dispatched"))
                  for t in paused]


async def final_review(conversation_id: str) -> str | None:
    if not on(CHECK) or await _in_flight(conversation_id):
        return None
    plan, stalled = await _stalled(conversation_id)
    if not stalled:
        return None
    reviewed = plan["reviewed"] if plan else bool(await fetch_one(
        "SELECT id FROM agent_harness_events WHERE conversation_id = %s AND kind = 'review' LIMIT 1", conversation_id))
    if not reviewed:
        if plan:
            await fetch_one("UPDATE agent_plans SET reviewed = true WHERE id = %s RETURNING id", plan["id"])
        await log_event(conversation_id, "review", f"{len(stalled)} open")
        listing = "\n".join(f"- {s['key']}: {reason}" for s, reason in stalled)
        return ("[HARNESS REVIEW] You are about to finish, but these subgoals fail their check:\n" + listing +
                "\nClose each one now (act, flag with report_exception, or revise the plan), or state why it stays open. "
                "Anything still open after this turn is reported as an unresolved exception.")
    for s, reason in stalled:
        await warehouse.raise_exception(UNRESOLVED, f"Unresolved subgoal {s['key']} ({s['title']}): {reason}", "warning",
                                        order_id=s.get("order_id"))
        if plan:
            await fetch_one("UPDATE agent_subgoals SET status = 'failed', note = %s WHERE id = %s RETURNING id",
                            "unresolved at final review", s["id"])
    await log_event(conversation_id, "unresolved", f"{len(stalled)} exception(s)")
    return None


SUBGOAL = {"type": "object", "properties": {
    "id": {"type": "string", "description": "Short unique id, e.g. o1042"},
    "title": {"type": "string"},
    "check": {"type": "string", "enum": list(CHECK_TYPES), "description":
              "Post-condition: order_dispatched (order completed), order_flagged (not shipped, exception recorded), "
              "order_untouched (left alone), task_not_paused (task finished), stock_put_away (none left in receiving)"},
    "order_id": {"type": "integer"}, "task_id": {"type": "integer"}, "sku": {"type": "string"},
    "depends_on": {"type": "array", "items": {"type": "string"}}},
    "required": ["id", "title", "check"]}


def plan_tools(tool_cls, params) -> list:
    return [
        tool_cls("create_plan",
                 "Write the plan for the manager's request: one subgoal per order or chore, each with a post-condition "
                 "check. Replaces any active plan.",
                 params({"objective": {"type": "string"}, "subgoals": {"type": "array", "items": SUBGOAL}},
                        ["objective", "subgoals"]), create_plan),
        tool_cls("update_subgoal",
                 "Set a subgoal's status (open, in_progress, done, failed, dropped), optionally changing its check or "
                 "target when the plan changes.",
                 params({"id": {"type": "string"}, "status": {"type": "string", "enum": list(STATUSES)},
                         "note": {"type": "string"}, "check": {"type": "string", "enum": list(CHECK_TYPES)},
                         "order_id": {"type": "integer"}, "task_id": {"type": "integer"}}, ["id", "status"]),
                 update_subgoal),
        tool_cls("add_subgoals", "Add subgoals to the active plan.",
                 params({"subgoals": {"type": "array", "items": SUBGOAL}}, ["subgoals"]), add_subgoals),
        tool_cls("get_plan", "Show the active plan.", params(), get_plan),
    ]


PLAN_TOOL_NAMES = {"create_plan", "update_subgoal", "add_subgoals", "get_plan"}


SUMMARY_PROMPT = (
    "You write handoff notes for a warehouse operations agent whose earlier messages are being removed to free "
    "context. From the transcript, list: decisions made, each order handled and its outcome, tasks created with their "
    "ids and last known status, exceptions reported, and anything still unresolved. Omit stock quantities (they may "
    "be stale). Plain bullets, at most 250 words."
)


def _transcript(messages: list[dict], limit_chars: int = 120_000) -> str:
    lines = []
    for message in messages:
        if message.get("role") == "tool":
            lines.append(f"<- {message.get('name')}: {str(message.get('content'))[:800]}")
            continue
        if message.get("content"):
            lines.append(f"{message['role']}: {message['content'][:1500]}")
        for call in message.get("tool_calls") or []:
            lines.append(f"-> {call['function']['name']}({call['function']['arguments'][:400]})")
    return "\n".join(lines)[-limit_chars:]


async def original_request(conversation_id: str) -> str:
    row = await fetch_one(
        "SELECT user_message FROM agent_runs WHERE conversation_id = %s ORDER BY id LIMIT 1", conversation_id)
    return row["user_message"] if row else ""


def needs_compaction(last_prompt_tokens: int, added_chars: int) -> bool:
    estimate = last_prompt_tokens + added_chars // 3
    return on(ANCHOR) and estimate >= settings.compaction_threshold * settings.llm_context_tokens


async def compact(client, conversation_id: str, messages: list[dict], ids: list[int | None],
                  keep: int = 6) -> tuple[list[dict], list[int | None]]:
    start = max(1, len(messages) - keep)
    while start > 1 and messages[start].get("role") == "tool":
        start -= 1
    if start <= 1:
        return messages, ids
    old = messages[1:start]
    response = await client.chat.completions.create(
        model=settings.llm_model, temperature=0.2, max_tokens=600,
        messages=[{"role": "system", "content": SUMMARY_PROMPT}, {"role": "user", "content": _transcript(old)}],
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    summary = response.choices[0].message.content or ""
    content = (f"[HARNESS CONTEXT RESET] Earlier messages were compacted.\nOriginal request: "
               f"{await original_request(conversation_id)}\nSummary so far:\n{summary}\n"
               "Stock numbers seen before this point may be stale; look them up again before relying on them.")
    if on(PLAN):
        content += "\n" + await plan_message(conversation_id)
    upto = max((i for i in ids[1:start] if i is not None), default=None)
    if upto is None:
        return messages, ids
    await fetch_one(
        "INSERT INTO agent_compactions (conversation_id, upto_message_id, content) VALUES (%s, %s, %s) RETURNING id",
        conversation_id, upto, content)
    await log_event(conversation_id, "compaction", f"{len(old)} messages")
    return [messages[0], {"role": "user", "content": content}, *messages[start:]], [None, None, *ids[start:]]


async def history(conversation_id: str) -> tuple[list[dict], list[int | None]]:
    compaction = await fetch_one(
        "SELECT upto_message_id, content FROM agent_compactions WHERE conversation_id = %s ORDER BY id DESC LIMIT 1",
        conversation_id) if on(ANCHOR) else None
    after = compaction["upto_message_id"] if compaction else 0
    rows = await fetch_all(
        "SELECT id, message FROM agent_messages WHERE conversation_id = %s AND id > %s ORDER BY id",
        conversation_id, after)
    messages, ids = [r["message"] for r in rows], [r["id"] for r in rows]
    if compaction:
        return [{"role": "user", "content": compaction["content"]}, *messages], [None, *ids]
    return messages, ids

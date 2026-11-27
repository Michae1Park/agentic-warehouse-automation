import argparse
import json
import time
import urllib.request
import uuid
from pathlib import Path

SCENARIOS = {
    "batch": "Ship every pending order for DISPATCH-1.",
    "triage": "Something is off in aisle C. Sort it out.",
    "competing": "Prepare orders #1040 and #1042 for dispatch.",
    "end_of_shift": "Get all pending DISPATCH-1 orders out before the truck leaves.",
    "stress": "Get all pending DISPATCH-1 orders out before the truck leaves.",
    "stress_xl": "Get all pending DISPATCH-1 orders out before the truck leaves.",
}
STRESS = ("stress", "stress_xl")
RECEIVING = "STAGING-1"


def request(api: str, path: str, body: dict | None = None, timeout: float = 900):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(api + path, data=data, headers={"content-type": "application/json"},
                                 method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read())


def settle(api: str, conversation: str, limit_s: float) -> bool:
    start, quiet = time.time(), 0
    while time.time() - start < limit_s:
        tasks = request(api, "/api/tasks?limit=200")
        busy = any(t["status"] == "running" for t in tasks)
        messages = request(api, f"/api/agent/conversations/{conversation}/messages")
        last = messages[-1] if messages else {}
        idle = last.get("role") == "assistant" and not last.get("tool_calls")
        quiet = quiet + 1 if idle and not busy else 0
        if quiet >= 3:
            return True
        time.sleep(5)
    return False


def snapshot(api: str) -> dict:
    orders = {o["id"]: o for o in request(api, "/api/orders")}
    tasks = request(api, "/api/tasks?limit=200")
    stock = {(l["id"], s["sku"]): s["quantity"] for l in request(api, "/api/locations") for s in l["stock"]}
    exceptions = request(api, "/api/exceptions")
    products = request(api, "/api/products")
    lines = {i: request(api, f"/api/orders/{i}").get("lines", []) for i in orders}
    return {"orders": orders, "tasks": tasks, "stock": stock, "exceptions": exceptions, "lines": lines,
            "products": products}


def task_for(state: dict, order_id: int) -> list[dict]:
    return [t for t in state["tasks"] if t.get("order_id") == order_id]


def flagged(state: dict, order_id: int) -> bool:
    return any(e.get("order_id") == order_id for e in state["exceptions"])


def physical_supply(initial: dict, missing: list[dict], pickable: set[str]) -> dict[str, int]:
    lost = {(m["location_id"], m["sku"]) for m in missing}
    supply: dict[str, int] = {}
    for (location_id, sku), quantity in initial["stock"].items():
        if sku in pickable and (location_id, sku) not in lost and not location_id.startswith("DISPATCH"):
            supply[sku] = supply.get(sku, 0) + quantity
    return supply


def score_stress(initial: dict, missing: list[dict], state: dict) -> dict[str, bool]:
    pickable = {p["sku"] for p in initial["products"] if p["robot_pickable"]}
    supply = physical_supply(initial, missing, pickable)
    orders = state["orders"]
    lines = {i: {l["sku"]: l["quantity"] for l in initial["lines"][i]} for i in orders}
    shift = [i for i, o in orders.items() if o["dispatch_location"] == "DISPATCH-1"]
    robot = [i for i in shift if set(lines[i]) <= pickable]
    manual = [i for i in shift if i not in robot]
    done = {i for i in robot if orders[i]["status"] == "completed"}

    def fits(order_id: int) -> bool:
        used: dict[str, int] = {}
        for other in done:
            if orders[other]["priority"] <= orders[order_id]["priority"]:
                for sku, quantity in lines[other].items():
                    used[sku] = used.get(sku, 0) + quantity
        return all(supply.get(sku, 0) - used.get(sku, 0) >= quantity for sku, quantity in lines[order_id].items())

    unfilled = [i for i in robot if i not in done]
    return {
        "priority respected and no fillable order left behind": not any(fits(i) for i in unfilled),
        "every unfilled order flagged": all(flagged(state, i) for i in unfilled),
        "manual orders not dispatched and flagged": all(not task_for(state, i) and flagged(state, i) for i in manual),
        "other dock untouched": all(orders[i]["status"] == "pending" and not task_for(state, i)
                                    for i in orders if i not in shift),
        "no task left paused": not any(t["status"] in ("planned", "queued", "running") for t in state["tasks"]),
        "cross-dock leftovers put away": all(
            state["stock"].get((RECEIVING, sku), 0) in (0, initial["stock"].get((RECEIVING, sku), 0)) for sku in pickable),
    }


def write_trace(path: Path, row: dict, messages: list[dict]) -> None:
    lines = [f"# {row['scenario']} · {row['variant']} · trial {row['trial']}", "",
             f"Success: {row['success']} · tool calls: {row['tool_calls']} · turns: {row['turns']} · wall: {row['wall_s']}s", ""]
    lines += [f"- {'PASS' if ok else 'FAIL'} {name}" for name, ok in row["checks"].items()]
    if row.get("missing"):
        lines += ["", "Hidden empty bins: " + ", ".join(f"{m['location_id']} {m['sku']}" for m in row["missing"])]
    lines += ["", "## Conversation", ""]
    for message in messages:
        if message.get("role") == "tool":
            lines.append(f"    <- {message.get('name')}: {str(message.get('content'))[:600]}")
            continue
        if message.get("content"):
            lines.append(f"**{message.get('role')}:** {message['content']}")
        for call in message.get("tool_calls") or []:
            lines.append(f"    -> {call['function']['name']}({call['function']['arguments']})")
        lines.append("")
    path.with_suffix(".md").write_text("\n".join(lines))
    path.with_suffix(".json").write_text(json.dumps({"row": row, "messages": messages}, indent=1, default=str))


def score(scenario: str, state: dict) -> dict[str, bool]:
    status = {i: o["status"] for i, o in state["orders"].items()}
    stock = state["stock"]
    succeeded = {t["kind"] for t in state["tasks"] if t["status"] == "succeeded"}
    if scenario == "batch":
        return {
            "all DISPATCH-1 orders completed": all(status[i] == "completed" for i in (1040, 1042, 1044, 1045, 1046, 1047)),
            "DISPATCH-2 orders untouched": all(status[i] == "pending" and not task_for(state, i) for i in (1041, 1043)),
        }
    if scenario == "triage":
        return {
            "aisle C counted": "inspection" in succeeded,
            "misplaced soup moved home": stock.get(("C-01-1", "SKU-1002"), 0) == 3 and "relocation" in succeeded,
            "missing mustard recorded": stock.get(("C-04-1", "SKU-1001"), 0) == 0,
        }
    if scenario == "competing":
        return {
            "P1 order 1042 completed": status[1042] == "completed",
            "P3 order 1040 not shipped": status[1040] != "completed",
            "1040 flagged": flagged(state, 1040),
        }
    if scenario == "end_of_shift":
        return {
            "P1 orders 1042 and 1045 completed": status[1042] == "completed" and status[1045] == "completed",
            "1044 completed via cross-dock": status[1044] == "completed",
            "1040 not shipped and flagged": status[1040] != "completed" and flagged(state, 1040),
            "non-pickable 1046 not dispatched and flagged": not task_for(state, 1046) and flagged(state, 1046),
            "surplus put away": stock.get(("STAGING-1", "SKU-1001"), 0) == 0 and "replenishment" in succeeded,
        }
    raise ValueError(scenario)


def main() -> None:
    parser = argparse.ArgumentParser(description="Score the agent's planning on multi-step scenarios (mock robot)")
    parser.add_argument("--api", default="http://localhost:8010")
    parser.add_argument("--scenarios", nargs="+", default=[s for s in SCENARIOS if s not in STRESS])
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--variants", nargs="+", default=["recipes", "no_recipes"])
    parser.add_argument("--time-scale", type=float, default=20)
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--limit-s", type=float, default=900)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--out", default=str(Path(__file__).parent / "results" / "agent"))
    args = parser.parse_args()

    out = Path(args.out)
    (out / "traces").mkdir(parents=True, exist_ok=True)
    results = out / "agent_results.json"
    rows = []
    if args.resume and results.exists():
        saved = json.loads(results.read_text())
        rows = [r for r in (saved["rows"] if isinstance(saved, dict) else saved) if r["settled"]]
    finished = {(r["scenario"], r["variant"], r["trial"]) for r in rows}
    for scenario in args.scenarios:
        for variant in args.variants:
            for trial in range(args.trials):
                if (scenario, variant, trial) in finished:
                    continue
                reset = request(args.api, "/api/sim/reset", {"seed": trial, "scenario": scenario, "time_scale": args.time_scale,
                                                     "agent_recipes": variant == "recipes",
                                                     "agent_temperature": args.temperature,
                                                             "cancel_queued": True})
                initial = snapshot(args.api) if scenario in STRESS else None
                conversation = f"eval-{scenario}-{variant}-{trial}-{uuid.uuid4().hex[:6]}"
                started = time.time()
                try:
                    request(args.api, f"/api/agent/conversations/{conversation}/messages", {"content": SCENARIOS[scenario]})
                    settled = settle(args.api, conversation, args.limit_s)
                except Exception as error:
                    settled = False
                    print(f"  error: {error}", flush=True)
                state = snapshot(args.api)
                checks = (score_stress(initial, reset["missing_stock"], state) if scenario in STRESS
                          else score(scenario, state))
                runs = request(args.api, "/api/agent/runs?limit=200")
                messages = request(args.api, f"/api/agent/conversations/{conversation}/messages")
                row = {
                    "scenario": scenario, "variant": variant, "trial": trial, "temperature": args.temperature, "settled": settled,
                    "success": settled and all(checks.values()), "checks": checks,
                    "tool_calls": sum(r.get("tool_calls") or 0 for r in runs),
                    "turns": len(runs), "wall_s": round(time.time() - started, 1),
                    "tools": [c["function"]["name"] for m in messages for c in (m.get("tool_calls") or [])],
                }
                if initial:
                    row["state"] = {name: {"orders": {i: {k: o[k] for k in ("status", "priority", "dispatch_location")}
                                                      for i, o in snap["orders"].items()},
                                           "lines": snap["lines"], "exceptions": snap["exceptions"],
                                           "tasks": [{k: t.get(k) for k in ("id", "kind", "status", "order_id")} for t in snap["tasks"]],
                                           "stock": [[l, k, q] for (l, k), q in snap["stock"].items()]}
                                    for name, snap in (("initial", initial), ("final", state))}
                    row["missing"] = reset["missing_stock"]
                write_trace(out / "traces" / f"{scenario}-{variant}-{trial}", row, messages)
                rows.append(row)
                print(f"{scenario:13s} {variant:10s} #{trial} success={row['success']} tools={row['tool_calls']} "
                      f"turns={row['turns']} {checks}", flush=True)
                (out / "agent_results.json").write_text(json.dumps(rows, indent=2))

    summary = {}
    for row in rows:
        key = f"{row['scenario']}/{row['variant']}"
        entry = summary.setdefault(key, {"trials": 0, "success": 0, "tool_calls": []})
        entry["trials"] += 1
        entry["success"] += row["success"]
        entry["tool_calls"].append(row["tool_calls"])
    for entry in summary.values():
        calls = entry.pop("tool_calls")
        entry["tool_calls_mean"] = round(sum(calls) / len(calls), 1)
        entry["tool_calls_max"] = max(calls)
    (out / "agent_results.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

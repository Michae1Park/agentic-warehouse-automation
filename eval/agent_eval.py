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
}


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
        busy = any(t["status"] in ("queued", "running") for t in tasks)
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
    return {"orders": orders, "tasks": tasks, "stock": stock, "exceptions": exceptions}


def task_for(state: dict, order_id: int) -> list[dict]:
    return [t for t in state["tasks"] if t.get("order_id") == order_id]


def flagged(state: dict, order_id: int) -> bool:
    return any(e.get("order_id") == order_id for e in state["exceptions"])


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
    parser.add_argument("--scenarios", nargs="+", default=list(SCENARIOS))
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--variants", nargs="+", default=["recipes", "no_recipes"])
    parser.add_argument("--time-scale", type=float, default=20)
    parser.add_argument("--limit-s", type=float, default=900)
    parser.add_argument("--out", default=str(Path(__file__).parent / "results" / "agent"))
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for scenario in args.scenarios:
        for variant in args.variants:
            for trial in range(args.trials):
                request(args.api, "/api/sim/reset", {"seed": trial, "scenario": scenario, "time_scale": args.time_scale,
                                                     "agent_recipes": variant == "recipes"})
                conversation = f"eval-{scenario}-{variant}-{trial}-{uuid.uuid4().hex[:6]}"
                started = time.time()
                try:
                    request(args.api, f"/api/agent/conversations/{conversation}/messages", {"content": SCENARIOS[scenario]})
                    settled = settle(args.api, conversation, args.limit_s)
                except Exception as error:
                    settled = False
                    print(f"  error: {error}", flush=True)
                state = snapshot(args.api)
                checks = score(scenario, state)
                runs = request(args.api, "/api/agent/runs?limit=200")
                row = {
                    "scenario": scenario, "variant": variant, "trial": trial, "settled": settled,
                    "success": settled and all(checks.values()), "checks": checks,
                    "tool_calls": sum(r.get("tool_calls") or 0 for r in runs),
                    "turns": len(runs), "wall_s": round(time.time() - started, 1),
                    "tools": [c["function"]["name"] for m in request(
                        args.api, f"/api/agent/conversations/{conversation}/messages")
                              for c in (m.get("tool_calls") or [])],
                }
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

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

FAULT_PROFILES = {
    "nominal": {"grasp_failure_rate": 0.0, "nav_block_rate": 0.0, "missing_stock_rate": 0.0},
    "faulty": {"grasp_failure_rate": 0.15, "nav_block_rate": 0.10, "missing_stock_rate": 0.20},
}

MODES = {
    "scripted_baseline": {"recovery_enabled": False, "route_optimization": False, "agent": False},
    "scripted_recovery": {"recovery_enabled": True, "route_optimization": True, "agent": False},
    "agent": {"recovery_enabled": True, "route_optimization": True, "agent": True},
}


class Api:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def call(self, method: str, path: str, body: dict | None = None, timeout: float = 300):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            self.base + path, data=data, method=method, headers={"content-type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read() or b"null")

    def get(self, path: str):
        return self.call("GET", path)[1]

    def post(self, path: str, body: dict | None = None):
        return self.call("POST", path, body or {})


@dataclass
class RunResult:
    mode: str
    profile: str
    seed: int
    orders_requested: int
    orders_completed: int
    orders_with_intervention: int
    exec_times_s: list[float] = field(default_factory=list)
    agent_turn_ms: list[float] = field(default_factory=list)
    agent_llm_ms: list[float] = field(default_factory=list)
    faults: int = 0
    faults_recovered: int = 0
    inventory_accuracy: float = 1.0


def wait_idle(api: Api, timeout: float = 600) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        tasks = api.get("/api/tasks?limit=200")
        runs = api.get("/api/agent/runs?limit=200")
        if not any(t["status"] in ("queued", "running") for t in tasks) and all(r["finished_at"] for r in runs):
            return
        time.sleep(0.5)
    raise TimeoutError("system did not become idle")


def run_scenario(api: Api, mode: str, profile: str, seed: int, orders: int, time_scale: float) -> RunResult:
    config = MODES[mode]
    wait_idle(api)
    status, body = api.post("/api/sim/reset", {
        "seed": seed, "generated_orders": orders, "faults": FAULT_PROFILES[profile],
        "recovery_enabled": config["recovery_enabled"], "route_optimization": config["route_optimization"],
        "time_scale": time_scale,
    })
    assert status == 200, body

    for order in api.get("/api/orders"):
        if config["agent"]:
            api.post(
                f"/api/agent/conversations/eval-{uuid.uuid4()}/messages",
                {"content": f"Prepare order #{order['id']} and move it to its dispatch area."},
            )
        else:
            api.post(f"/api/orders/{order['id']}/fulfill")
        wait_idle(api)

    final_orders = api.get("/api/orders")
    tasks = api.get("/api/tasks?limit=500")
    exceptions = api.get("/api/exceptions")
    kpis = api.get("/api/kpis")
    accuracy = api.get("/api/sim/inventory-accuracy")
    runs = [r for r in api.get("/api/agent/runs?limit=500") if not r["user_message"].startswith("[SYSTEM EVENT]")]

    def seconds(task: dict) -> float:
        start = datetime.fromisoformat(task["started_at"])
        end = datetime.fromisoformat(task["finished_at"])
        return (end - start).total_seconds() * time_scale

    return RunResult(
        mode=mode, profile=profile, seed=seed,
        orders_requested=len(final_orders),
        orders_completed=sum(o["status"] == "completed" for o in final_orders),
        orders_with_intervention=len({e["order_id"] for e in exceptions if e["status"] != "resolved" and e["order_id"]}),
        exec_times_s=[seconds(t) for t in tasks if t["status"] == "succeeded"],
        agent_turn_ms=[
            (datetime.fromisoformat(r["finished_at"]) - datetime.fromisoformat(r["started_at"])).total_seconds() * 1000
            for r in runs
        ],
        agent_llm_ms=[r["llm_ms"] for r in runs],
        faults=kpis["faults"], faults_recovered=kpis["faults_recovered"],
        inventory_accuracy=accuracy["accuracy"],
    )


def mean(values: list[float]) -> float | None:
    return statistics.mean(values) if values else None


def summarize(results: list[RunResult]) -> list[dict]:
    rows = []
    for mode in MODES:
        for profile in FAULT_PROFILES:
            group = [r for r in results if r.mode == mode and r.profile == profile]
            if not group:
                continue
            requested = sum(r.orders_requested for r in group)
            faults = sum(r.faults for r in group)
            rows.append({
                "mode": mode, "profile": profile, "runs": len(group), "orders": requested,
                "fulfillment_success": sum(r.orders_completed for r in group) / requested,
                "interventions_per_order": sum(r.orders_with_intervention for r in group) / requested,
                "avg_execution_s": mean([t for r in group for t in r.exec_times_s]),
                "inventory_accuracy": mean([r.inventory_accuracy for r in group]),
                "faults": faults,
                "recovery_success": sum(r.faults_recovered for r in group) / faults if faults else None,
                "avg_agent_turn_s": (mean([t for r in group for t in r.agent_turn_ms]) or 0) / 1000 or None,
                "avg_agent_llm_s": (mean([t for r in group for t in r.agent_llm_ms]) or 0) / 1000 or None,
            })
    return rows


def fmt(value, kind: str) -> str:
    if value is None:
        return "—"
    return {"pct": f"{value:.0%}", "float": f"{value:.2f}", "s": f"{value:.1f}s"}[kind]


def to_markdown(rows: list[dict], meta: dict) -> str:
    header = (
        "| Mode | Faults | Orders | Fulfillment success | Interventions / order | Avg execution (sim) "
        "| Inventory accuracy | Recovery success | Agent turn latency | Agent LLM time |\n"
        "|---|---|---|---|---|---|---|---|---|---|\n"
    )
    body = "".join(
        f"| {r['mode']} | {r['profile']} | {r['orders']} | {fmt(r['fulfillment_success'], 'pct')} "
        f"| {fmt(r['interventions_per_order'], 'float')} | {fmt(r['avg_execution_s'], 's')} "
        f"| {fmt(r['inventory_accuracy'], 'pct')} | {fmt(r['recovery_success'], 'pct')} "
        f"({r['faults']} faults) | {fmt(r['avg_agent_turn_s'], 's')} | {fmt(r['avg_agent_llm_s'], 's')} |\n"
        for r in rows
    )
    notes = (
        f"\nSimulated results against a scripted simulation baseline, not a real warehouse. "
        f"Seeds {meta['seeds']}, {meta['orders']} generated orders per run, mock robot at "
        f"{meta['time_scale']}x time scale (execution times reported in simulated seconds). "
        f"Fault profile `faulty`: {FAULT_PROFILES['faulty']}.\n"
    )
    return header + body + notes


def main() -> None:
    parser = argparse.ArgumentParser(description="Run KPI evaluation against the warehouse backend")
    parser.add_argument("--api", default="http://localhost:8010")
    parser.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    parser.add_argument("--orders", type=int, default=8)
    parser.add_argument("--time-scale", type=float, default=20)
    parser.add_argument("--modes", nargs="+", default=list(MODES))
    parser.add_argument("--profiles", nargs="+", default=list(FAULT_PROFILES))
    parser.add_argument("--out", default=str(Path(__file__).parent / "results"))
    args = parser.parse_args()

    api = Api(args.api)
    results = []
    for mode in args.modes:
        for profile in args.profiles:
            for seed in args.seeds:
                started = time.time()
                result = run_scenario(api, mode, profile, seed, args.orders, args.time_scale)
                results.append(result)
                print(f"{mode:18} {profile:8} seed={seed} completed {result.orders_completed}/{result.orders_requested} "
                      f"faults {result.faults_recovered}/{result.faults} ({time.time() - started:.0f}s)", flush=True)

    rows = summarize(results)
    meta = {"seeds": args.seeds, "orders": args.orders, "time_scale": args.time_scale,
            "timestamp": datetime.now().isoformat(timespec="seconds")}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps({"meta": meta, "summary": rows, "runs": [asdict(r) for r in results]}, indent=2))
    (out / "results.md").write_text(to_markdown(rows, meta))
    print(to_markdown(rows, meta))


if __name__ == "__main__":
    main()

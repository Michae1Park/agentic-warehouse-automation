import argparse
import json
import sys
import time
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from run_eval import FAULT_PROFILES, Api  # noqa: E402

STUCK_TIMEOUT_S = 1200


def wait_idle(api: Api, sim: str, timeout: float = STUCK_TIMEOUT_S) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        tasks = api.get("/api/tasks?limit=200")
        runs = api.get("/api/agent/runs?limit=200")
        if not any(t["status"] in ("queued", "running") for t in tasks) and all(r["finished_at"] for r in runs):
            return True
        time.sleep(2)
    return False


def sim_alive(sim: str) -> bool:
    try:
        return Api(sim).get("/health")["status"] == "ok"
    except Exception:
        return False


def workflow_requests(api: Api) -> list[tuple[str, str]]:
    products = {p["sku"]: p["name"] for p in api.get("/api/products")}
    inventory = api.get("/api/inventory/locations")
    requests = [("fulfillment", f"Prepare order #{o['id']} and move it to its dispatch area.")
                for o in api.get("/api/orders") if o["status"] == "pending"]
    staged = next((r for r in inventory if r["location_id"].startswith("STAGING") and r["quantity"] >= 2), None)
    if staged:
        requests.append(("replenishment", f"Replenish 2 units of {products[staged['sku']]} from receiving."))
    stocked = next((r for r in inventory if r["location_id"][0] in "ABC" and r["quantity"] - r["reserved"] >= 4), None)
    if stocked:
        requests.append(("relocation", f"Move 2 units of {products[stocked['sku']]} from {stocked['location_id']} "
                                       "to an empty bin."))
    requests.append(("cycle_count", "Cycle count aisle B."))
    return requests


def run_seed(api: Api, sim: str, seed: int, profile: str, orders: int) -> dict:
    assert wait_idle(api, sim), "system not idle before reset"
    status, body = api.post("/api/sim/reset", {
        "seed": seed, "generated_orders": orders, "faults": FAULT_PROFILES[profile],
        "recovery_enabled": True, "route_optimization": True,
    })
    assert status == 200, body
    results = []
    for workflow, message in workflow_requests(api):
        before = {t["id"] for t in api.get("/api/tasks?limit=500")}
        started = time.time()
        status, reply = api.call("POST", f"/api/agent/conversations/soak-{uuid.uuid4()}/messages",
                                 {"content": message}, timeout=STUCK_TIMEOUT_S)
        idle = wait_idle(api, sim)
        new_tasks = [t for t in api.get("/api/tasks?limit=500") if t["id"] not in before]
        results.append({
            "workflow": workflow, "message": message, "http_status": status, "idle": idle,
            "sim_alive": sim_alive(sim), "seconds": round(time.time() - started, 1),
            "tasks": [{"id": t["id"], "kind": t["kind"], "status": t["status"], "error": t["error"]} for t in new_tasks],
        })
        print(f"seed={seed} {profile:7} {workflow:13} http={status} idle={idle} "
              f"tasks={[t['status'] for t in new_tasks]} ({results[-1]['seconds']}s)", flush=True)
        if not idle or not results[-1]["sim_alive"]:
            break

    audits = []
    for task in api.get("/api/tasks?limit=500"):
        detail = api.get(f"/api/tasks/{task['id']}")
        for step in detail.get("steps", []):
            audit = (step.get("result") or {}).get("tote_audit")
            if audit:
                audits.append(audit)
    exceptions = api.get("/api/exceptions")
    return {
        "seed": seed, "profile": profile, "requests": results,
        "orders": Counter(o["status"] for o in api.get("/api/orders")),
        "exceptions": Counter(e["type"] for e in exceptions),
        "tote_audits": len(audits), "tote_audit_false_alarms": sum(not a["passed"] for a in audits),
        "inventory_accuracy": api.get("/api/sim/inventory-accuracy")["accuracy"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Soak-test every scoped workflow through the agent against Isaac")
    parser.add_argument("--api", default="http://localhost:8010")
    parser.add_argument("--sim", default="http://localhost:8020")
    parser.add_argument("--runs", nargs="+", default=["1:faulty", "2:faulty", "3:nominal"])
    parser.add_argument("--orders", type=int, default=4)
    parser.add_argument("--out", default=str(Path(__file__).parent / "results" / "soak"))
    args = parser.parse_args()

    api = Api(args.api)
    seeds = []
    for run in args.runs:
        seed, profile = run.split(":")
        seeds.append(run_seed(api, args.sim, int(seed), profile, args.orders))

    requests = [r for s in seeds for r in s["requests"]]
    tasks = [t for r in requests for t in r["tasks"]]
    summary = {
        "requests": len(requests),
        "requests_without_task": sum(not r["tasks"] for r in requests),
        "stuck": sum(not r["idle"] for r in requests),
        "sim_crashes": sum(not r["sim_alive"] for r in requests),
        "http_errors": sum(r["http_status"] != 200 for r in requests),
        "tasks": dict(Counter(t["status"] for t in tasks)),
        "task_errors": [t["error"] for t in tasks if t["error"]],
        "tote_audits": sum(s["tote_audits"] for s in seeds),
        "tote_audit_false_alarms": sum(s["tote_audit_false_alarms"] for s in seeds),
        "inventory_accuracy": [s["inventory_accuracy"] for s in seeds],
    }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "soak_results.json").write_text(json.dumps(
        {"timestamp": datetime.now().isoformat(timespec="seconds"), "summary": summary, "runs": seeds}, indent=2,
        default=dict))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

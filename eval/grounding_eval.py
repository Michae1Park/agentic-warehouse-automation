import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from run_eval import Api  # noqa: E402


def queries(product: dict) -> dict[str, str]:
    noun = product["name"].split()[-1].lower()
    return {
        "name": product["name"].lower(),
        "description": product["description"],
        "attribute": f"the {product['color']} {noun}",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="VLM grounding accuracy: does the VLM point at the described item?")
    parser.add_argument("--api", default="http://localhost:8010")
    parser.add_argument("--seeds", nargs="+", type=int, default=[1, 2, 3])
    parser.add_argument("--out", default=str(Path(__file__).parent / "results" / "grounding"))
    args = parser.parse_args()

    api = Api(args.api)
    rows = []
    for seed in args.seeds:
        status, body = api.post("/api/sim/reset", {"seed": seed})
        assert status == 200, body
        products = {p["sku"]: p for p in api.get("/api/products")}
        stocked = sorted({(r["location_id"], r["sku"]) for r in api.get("/api/inventory/locations")
                          if r["quantity"] > 0 and r["location_id"][0] in "ABC"})
        for location_id, sku in stocked:
            for kind, query in queries(products[sku]).items():
                status, result = api.call("POST", "/api/perception/ground",
                                          {"location_id": location_id, "query": query}, timeout=300)
                hit = (result.get("hit") or {}) if status == 200 else {}
                row = {"seed": seed, "location_id": location_id, "sku": sku, "kind": kind, "query": query,
                       "point": result.get("point"), "hit_sku": hit.get("sku"), "hit_object": hit.get("object"),
                       "correct": hit.get("sku") == sku, "latency_ms": result.get("latency_ms")}
                rows.append(row)
                print(json.dumps(row), flush=True)

    by_kind: dict[str, list[bool]] = defaultdict(list)
    by_sku: dict[str, list[bool]] = defaultdict(list)
    confusions: dict[str, int] = defaultdict(int)
    for row in rows:
        by_kind[row["kind"]].append(row["correct"])
        by_sku[row["sku"]].append(row["correct"])
        if not row["correct"]:
            confusions[f"{row['sku']} -> {row['hit_object'] or 'none'}"] += 1
    summary = {
        "queries": len(rows),
        "accuracy": sum(r["correct"] for r in rows) / len(rows),
        "by_kind": {k: sum(v) / len(v) for k, v in by_kind.items()},
        "by_sku": {k: sum(v) / len(v) for k, v in by_sku.items()},
        "confusions": dict(confusions),
        "mean_latency_ms": sum(r["latency_ms"] or 0 for r in rows) / len(rows),
    }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "grounding_results.json").write_text(json.dumps(
        {"timestamp": datetime.now().isoformat(timespec="seconds"), "summary": summary, "rows": rows}, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

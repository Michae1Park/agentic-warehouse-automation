import argparse
import base64
import json
import statistics
import urllib.request
from pathlib import Path


def request(url: str, body: dict | None = None, timeout: float = 120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"content-type": "application/json"},
                                 method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read())


def main() -> None:
    parser = argparse.ArgumentParser(description="Score VLM shelf perception against simulator ground truth")
    parser.add_argument("--api", default="http://localhost:8010")
    parser.add_argument("--sim", default="http://localhost:8020")
    parser.add_argument("--missing-rate", type=float, default=0.25)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", default=str(Path(__file__).parent / "results"))
    args = parser.parse_args()

    request(f"{args.api}/api/sim/reset", {"seed": args.seed, "faults": {"missing_stock_rate": args.missing_rate}})
    locations = [l for l in request(f"{args.api}/api/locations") if l["kind"] == "storage"]

    rows = []
    out = Path(args.out)
    (out / "vlm_images").mkdir(parents=True, exist_ok=True)
    for location in locations:
        shot = request(f"{args.sim}/inspect", {"location_id": location["id"]})
        image = base64.b64decode(shot["image_jpeg_b64"])
        (out / "vlm_images" / f"{location['id']}.jpg").write_bytes(image)
        vision = request(f"{args.api}/api/perception/identify",
                         {"location_id": location["id"], "image_jpeg_b64": shot["image_jpeg_b64"]})
        latency = vision["latency_ms"] / 1000
        predicted = vision["counts"]
        truth = shot["counts"]
        recorded = {s["sku"]: s["quantity"] for s in location["stock"] if s["quantity"] > 0}
        skus = set(truth) | set(predicted)
        rows.append({
            "location_id": location["id"], "truth": truth, "wms_record": recorded,
            "record_wrong": recorded != truth, "predicted": predicted, "latency_s": latency,
            "presence_correct": set(truth) == set(predicted),
            "count_exact": truth == predicted,
            "abs_count_error": sum(abs(truth.get(s, 0) - predicted.get(s, 0)) for s in skus),
            "units": sum(truth.values()),
        })
        print(f"{location['id']}: truth={truth} wms={recorded} predicted={predicted} ({latency:.1f}s)", flush=True)

    summary = {
        "bins": len(rows),
        "presence_accuracy": sum(r["presence_correct"] for r in rows) / len(rows),
        "exact_count_accuracy": sum(r["count_exact"] for r in rows) / len(rows),
        "mean_abs_count_error_visible_facings_only": statistics.mean(r["abs_count_error"] for r in rows),
        "mean_latency_s": statistics.mean(r["latency_s"] for r in rows),
        "bins_with_wrong_record": sum(r["record_wrong"] for r in rows),
        "discrepancies_detected": sum(r["record_wrong"] and r["presence_correct"] for r in rows),
    }
    (out / "vlm_results.json").write_text(json.dumps({"summary": summary, "bins": rows}, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

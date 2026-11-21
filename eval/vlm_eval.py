import argparse
import base64
import json
import re
import statistics
import time
import urllib.request
from pathlib import Path

PROMPT = """You are the vision system of a warehouse picking robot. The image shows one shelf bin.
Each product is a box with a distinct solid color:
{catalog}
Identify which products are present in the bin and count the boxes of each.
Reply with JSON only, mapping SKU to box count, e.g. {{"SKU-1001": 3}}. Use {{}} if the bin is empty."""


def request(url: str, body: dict | None = None, timeout: float = 120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"content-type": "application/json"},
                                 method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read())


def parse(text: str) -> dict[str, int]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        data = json.loads(match.group(0)) if match else {}
    except json.JSONDecodeError:
        return {}
    return {str(k): int(v) for k, v in data.items() if str(k).startswith("SKU-") and int(v) > 0}


def main() -> None:
    parser = argparse.ArgumentParser(description="Score VLM shelf perception against simulator ground truth")
    parser.add_argument("--api", default="http://localhost:8010")
    parser.add_argument("--sim", default="http://localhost:8020")
    parser.add_argument("--llm", default="http://localhost:8001/v1")
    parser.add_argument("--model", default="qwen3.5-9b")
    parser.add_argument("--out", default=str(Path(__file__).parent / "results"))
    args = parser.parse_args()

    products = request(f"{args.api}/api/products")
    catalog = "\n".join(f"- {p['color']} box = {p['sku']} ({p['name']})" for p in products)
    locations = [l for l in request(f"{args.api}/api/locations") if l["kind"] == "storage"]

    rows = []
    out = Path(args.out)
    (out / "vlm_images").mkdir(parents=True, exist_ok=True)
    for location in locations:
        shot = request(f"{args.sim}/inspect", {"location_id": location["id"]})
        image = base64.b64decode(shot["image_jpeg_b64"])
        (out / "vlm_images" / f"{location['id']}.jpg").write_bytes(image)
        started = time.perf_counter()
        response = request(f"{args.llm}/chat/completions", {
            "model": args.model, "temperature": 0.0, "max_tokens": 200,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + shot["image_jpeg_b64"]}},
                {"type": "text", "text": PROMPT.format(catalog=catalog)},
            ]}],
        })
        latency = time.perf_counter() - started
        predicted = parse(response["choices"][0]["message"]["content"] or "")
        truth = shot["counts"]
        skus = set(truth) | set(predicted)
        rows.append({
            "location_id": location["id"], "truth": truth, "predicted": predicted, "latency_s": latency,
            "presence_correct": set(truth) == set(predicted),
            "count_exact": truth == predicted,
            "abs_count_error": sum(abs(truth.get(s, 0) - predicted.get(s, 0)) for s in skus),
            "units": sum(truth.values()),
        })
        print(f"{location['id']}: truth={truth} predicted={predicted} ({latency:.1f}s)", flush=True)

    summary = {
        "bins": len(rows),
        "presence_accuracy": sum(r["presence_correct"] for r in rows) / len(rows),
        "exact_count_accuracy": sum(r["count_exact"] for r in rows) / len(rows),
        "mean_abs_count_error": statistics.mean(r["abs_count_error"] for r in rows),
        "mean_latency_s": statistics.mean(r["latency_s"] for r in rows),
    }
    (out / "vlm_results.json").write_text(json.dumps({"summary": summary, "bins": rows}, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

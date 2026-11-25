#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
ATTEMPTS="${ATTEMPTS:-15}"
while docker ps -q --filter ancestor=nvcr.io/nvidia/isaac-sim:5.1.0 | grep -q .; do sleep 60; done
for speed in 0.03 0.05 0.08; do
  out="eval/results/push_speed/$speed"
  rm -rf "$out"; mkdir -p "$out"
  ISAAC_MOUNT=$PWD taskset -c 16-31 isaac/run.sh isaac/warehouse_sim.py --collect-push "$ATTEMPTS" --push-speed "$speed" \
    --seed 11 --out "/workspace/$out" > "$out/run.log" 2>&1 || true
done
python3 - <<'PY'
import json, pathlib
for d in sorted(pathlib.Path("eval/results/push_speed").iterdir()):
    rows = [json.loads(l) for l in (d / "collect_log.jsonl").read_text().splitlines()]
    saved = sum(r["saved"] for r in rows)
    reasons = {}
    for r in rows:
        if not r["saved"]:
            key = "skipped" if "skipped" in r else "toppled" if not r.get("upright") else "short" if r["moved"] < r["goal"] else "depth" if abs(r["depth"]) > 0.03 else "target"
            reasons[key] = reasons.get(key, 0) + 1
    print(f"{d.name} m/s: saved {saved}/{len(rows)} attempts, rejected {reasons}")
PY

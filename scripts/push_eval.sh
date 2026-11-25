#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
VLA_PYTHON="${VLA_PYTHON:-$HOME/workspace/robo-learning/.venv/bin/python}"
CHECKPOINT="${CHECKPOINT:-vla/outputs/smolvla_push/checkpoints/last/pretrained_model}"
TRIALS="${TRIALS:-30}"
SEED="${SEED:-99}"
OUT="${OUT:-eval/results/push_vla}"
rm -rf "$OUT"; mkdir -p "$OUT"
taskset -c 16-31 "$VLA_PYTHON" vla/policy_server.py --checkpoint "$CHECKPOINT" > "$OUT/policy_server.log" 2>&1 &
server=$!
trap 'kill $server 2>/dev/null' EXIT
until curl -sf localhost:8030/health >/dev/null || curl -sf localhost:8030/ >/dev/null; do
  kill -0 $server 2>/dev/null || { echo "policy server died" >&2; tail "$OUT/policy_server.log" >&2; exit 1; }
  sleep 2
done
ISAAC_MOUNT=$PWD isaac/run.sh isaac/warehouse_sim.py --collect-push "$TRIALS" --push-policy vla --seed "$SEED" \
  --out "/workspace/$OUT" > "$OUT/run.log" 2>&1 || true
python3 - "$OUT" <<'PY'
import json, sys, pathlib
rows = [json.loads(l) for l in (pathlib.Path(sys.argv[1]) / "collect_log.jsonl").read_text().splitlines() if l.strip()]
trials = [r for r in rows if "skipped" not in r]
by = {}
for r in trials:
    by.setdefault(r["blocker"], [0, 0]); by[r["blocker"]][1] += 1; by[r["blocker"]][0] += r["success"]
print(f"push VLA: {sum(r['success'] for r in trials)}/{len(trials)} success, {len(rows) - len(trials)} skipped, by blocker {by}")
PY

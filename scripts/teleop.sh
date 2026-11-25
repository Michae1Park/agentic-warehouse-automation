#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
VLA_PYTHON="${VLA_PYTHON:-$HOME/workspace/robo-learning/.venv/bin/python}"
CHECKPOINT="${CHECKPOINT:-vla/outputs/smolvla_push_marker/checkpoints/last/pretrained_model}"
OUT="${OUT:-vla/data/push_teleop}"
SEED="${SEED:-7}"
mkdir -p "$OUT"
taskset -c 16-31 "$VLA_PYTHON" vla/policy_server.py --checkpoint "$CHECKPOINT" > "$OUT/policy_server.log" 2>&1 &
server=$!
trap 'kill $server 2>/dev/null' EXIT
ISAAC_MOUNT=$PWD isaac/run.sh isaac/warehouse_sim.py --teleop --marker-state --seed "$SEED" --out "/workspace/$OUT" 2>&1 | tee -a "$OUT/run.log"

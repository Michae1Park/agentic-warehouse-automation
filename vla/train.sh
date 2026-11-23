#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

VLA_PYTHON="${VLA_PYTHON:-$HOME/workspace/robo-learning/.venv/bin/python}"
CPUS="${CPUS:-16-31}"
STEPS="${STEPS:-8000}"
OUTPUT="${OUTPUT:-vla/outputs/smolvla_ycb}"

if docker compose ps --status running --services 2>/dev/null | grep -qx llm; then
  echo "Stop the LLM before training (GPU budget): docker compose stop llm" >&2
  exit 1
fi

taskset -c "$CPUS" "$VLA_PYTHON" vla/convert_dataset.py
taskset -c "$CPUS" "$VLA_PYTHON" -m lerobot.scripts.lerobot_train \
  --policy.path=lerobot/smolvla_base \
  --policy.push_to_hub=false \
  --dataset.repo_id=local/warehouse_ycb_grasp \
  --dataset.root=vla/data/lerobot_ycb \
  --rename_map='{"observation.images.head": "observation.images.camera1"}' \
  --batch_size=32 \
  --steps="$STEPS" \
  --save_freq=2000 \
  --num_workers=8 \
  --output_dir="$OUTPUT" 2>&1 | tee vla/train_ycb.log

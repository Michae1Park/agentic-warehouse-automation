#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

VLA_PYTHON="${VLA_PYTHON:-$HOME/workspace/robo-learning/.venv/bin/python}"
CPUS="${CPUS:-16-31}"
POLICY="${POLICY:-smolvla}"
STEPS="${STEPS:-8000}"
OUTPUT="${OUTPUT:-vla/outputs/${POLICY}_scenes}"

if docker compose ps --status running --services 2>/dev/null | grep -qx llm; then
  echo "Stop the LLM before training (GPU budget): docker compose stop llm" >&2
  exit 1
fi

[[ -d vla/data/lerobot_scenes ]] || taskset -c "$CPUS" "$VLA_PYTHON" vla/convert_dataset.py

if [[ "$POLICY" == smolvla ]]; then
  POLICY_ARGS=(--policy.path=lerobot/smolvla_base
               --rename_map='{"observation.images.head": "observation.images.camera1", "observation.images.wrist": "observation.images.camera2"}')
else
  POLICY_ARGS=(--policy.type=diffusion --policy.n_obs_steps=1 --policy.horizon=16 --policy.n_action_steps=8
               --policy.crop_shape='[240, 240]')
fi
taskset -c "$CPUS" "$VLA_PYTHON" -m lerobot.scripts.lerobot_train \
  "${POLICY_ARGS[@]}" \
  --policy.push_to_hub=false \
  --dataset.repo_id=local/warehouse_scenes_grasp \
  --dataset.root=vla/data/lerobot_scenes \
  --batch_size=32 \
  --steps="$STEPS" \
  --save_freq=2000 \
  --num_workers=8 \
  --output_dir="$OUTPUT" 2>&1 | tee vla/train_${POLICY}_scenes.log

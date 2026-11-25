#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

CPUS="${CPUS:-16-31}"
GRASP_POLICY="${GRASP_POLICY:-scripted}"
PUSH_POLICY="${PUSH_POLICY:-vla}"
GRAPHICS="${GRAPHICS:-high}"
CHECKPOINT="${CHECKPOINT:-vla/outputs/smolvla_push/checkpoints/last/pretrained_model}"
VLA_PYTHON="${VLA_PYTHON:-$HOME/workspace/robo-learning/.venv/bin/python}"

LLM_SETTINGS=$(grep -E "^LLM_(WEIGHTS|MODEL|GPU_UTIL|ARGS)=" .env 2>/dev/null || true)
cat > .env <<EOF
BACKEND_PORT=8010
LLM_PORT=8001
ROBOT_BACKEND=isaac
SIM_URL=http://localhost:8020
ISAAC_ARGS="--livestream --graphics $GRAPHICS --scenario hard"
GRASP_POLICY=$GRASP_POLICY
PUSH_POLICY=$PUSH_POLICY
$LLM_SETTINGS
EOF

ISAAC_VRAM_MB=9000
VLA_VRAM_MB=2000
read -r TOTAL_MB USED_MB < <(nvidia-smi --query-gpu=memory.total,memory.used --format=csv,noheader,nounits | tr -d ',')
ISAAC_PID_MB=$(nvidia-smi --query-compute-apps=process_name,used_memory --format=csv,noheader,nounits | awk -F', ' '/isaac-sim/ {s += $2} END {print s + 0}')
FREE_MB=$((TOTAL_MB - USED_MB + ISAAC_PID_MB))
NEED_MB=$ISAAC_VRAM_MB
[[ "$GRASP_POLICY" != scripted || "$PUSH_POLICY" == vla ]] && NEED_MB=$((NEED_MB + VLA_VRAM_MB))
if ! docker compose ps --status running --services 2>/dev/null | grep -qx llm; then
  LLM_UTIL=$(grep -oP '^LLM_GPU_UTIL=\K.*' .env || echo 0.75)
  NEED_MB=$((NEED_MB + $(awk -v u="$LLM_UTIL" -v t="$TOTAL_MB" 'BEGIN {printf "%d", u * t}')))
fi
if (( NEED_MB > FREE_MB )); then
  echo "GPU memory budget exceeded: need ~${NEED_MB} MiB, ${FREE_MB} MiB available." >&2
  echo "Lower LLM_GPU_UTIL / use a smaller LLM_WEIGHTS in .env, or stop other GPU jobs:" >&2
  nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader >&2
  exit 1
fi

pin_containers() {
  for container in $(docker compose --profile isaac ps -q); do
    docker update --cpuset-cpus "$CPUS" "$container" > /dev/null
  done
}

taskset -c "$CPUS" docker compose up -d
pin_containers

echo "Waiting for the LLM (first start ~3-5 min, cached restarts ~1 min)..."
until curl -sf "http://localhost:8001/v1/models" > /dev/null; do
  if [[ "$(docker inspect -f '{{.State.Status}}' "$(docker compose ps -aq llm)")" != running ]]; then
    echo "LLM container stopped during startup:" >&2
    docker compose logs --tail 20 llm >&2
    exit 1
  fi
  sleep 5
done

if [[ "$GRASP_POLICY" != scripted || "$PUSH_POLICY" == vla ]]; then
  taskset -c "$CPUS" nohup "$VLA_PYTHON" vla/policy_server.py --checkpoint "$CHECKPOINT" > vla/policy_server.log 2>&1 &
fi

taskset -c "$CPUS" docker compose --profile isaac up -d --force-recreate --no-deps isaac
pin_containers
echo "Waiting for Isaac Sim..."
until curl -sf "http://localhost:8020/health" > /dev/null; do sleep 5; done

IP=$(hostname -I | awk '{print $1}')
cat <<EOF

Ready.
  Dashboard:      http://$IP:5173
  WebRTC client:  server $IP · signal 49100 · stream 47998
  Try in chat:    Prepare order #1042 and move it to the dispatch area
  Stop:           docker compose --profile isaac down
EOF

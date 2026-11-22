#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

CPUS="${CPUS:-16-31}"
GRASP_POLICY="${GRASP_POLICY:-scripted}"
CHECKPOINT="vla/outputs/smolvla_grasp/checkpoints/last/pretrained_model"
VLA_PYTHON="${VLA_PYTHON:-$HOME/workspace/robo-learning/.venv/bin/python}"

LLM_SETTINGS=$(grep -E "^LLM_(WEIGHTS|MODEL|GPU_UTIL|ARGS)=" .env 2>/dev/null || true)
cat > .env <<EOF
BACKEND_PORT=8010
LLM_PORT=8001
ROBOT_BACKEND=isaac
SIM_URL=http://localhost:8020
ISAAC_ARGS="--livestream --graphics high"
GRASP_POLICY=$GRASP_POLICY
$LLM_SETTINGS
EOF

if [[ "$GRASP_POLICY" != scripted ]]; then
  taskset -c "$CPUS" nohup "$VLA_PYTHON" vla/policy_server.py --checkpoint "$CHECKPOINT" > vla/policy_server.log 2>&1 &
fi

taskset -c "$CPUS" docker compose --profile isaac up -d
taskset -c "$CPUS" docker compose --profile isaac up -d --force-recreate --no-deps isaac
for container in $(docker compose --profile isaac ps -q); do
  docker update --cpuset-cpus "$CPUS" "$container" > /dev/null
done

echo "Waiting for the LLM (first start ~3-5 min, cached restarts ~1 min)..."
until curl -sf "http://localhost:8001/v1/models" > /dev/null; do sleep 5; done
echo "Waiting for Isaac Sim..."
until curl -sf "http://localhost:8020/" > /dev/null || docker compose logs isaac 2>&1 | grep -q "app ready"; do sleep 5; done

IP=$(hostname -I | awk '{print $1}')
cat <<EOF

Ready.
  Dashboard:      http://$IP:5173
  WebRTC client:  server $IP · signal 49100 · stream 47998
  Try in chat:    Prepare order #1042 and move it to the dispatch area
  Stop:           docker compose --profile isaac down
EOF

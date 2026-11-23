#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CACHE="${ISAAC_CACHE:-$HOME/.cache/isaac-sim}"
mkdir -p "$CACHE"/{kit,ov,pip,glcache,computecache,data}
MOUNT="${ISAAC_MOUNT:-$SCRIPT_DIR}"
exec docker run --rm ${ISAAC_DOCKER_ARGS:-} -u 0 --gpus all --network host \
  -e ACCEPT_EULA=Y -e PRIVACY_CONSENT=Y -e OMNI_KIT_ALLOW_ROOT=1 \
  -v "$CACHE/kit:/isaac-sim/kit/cache" -v "$CACHE/ov:/root/.cache/ov" -v "$CACHE/pip:/root/.cache/pip" \
  -v "$CACHE/glcache:/root/.cache/nvidia/GLCache" -v "$CACHE/computecache:/root/.nv/ComputeCache" \
  -v "$CACHE/data:/root/.local/share/ov/data" -v "$MOUNT:/workspace" -w /workspace \
  --entrypoint /isaac-sim/python.sh nvcr.io/nvidia/isaac-sim:5.1.0 "$@"

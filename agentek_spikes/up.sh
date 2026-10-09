#!/usr/bin/env bash
# Starts the mock upstream and one LiteLLM gateway container on the poolspike network.
# usage: up.sh [gateway-name] [host-port] [image]   (extra env via GW_ENV="-e K=V ...")
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
NAME="${1:-gw1}"; PORT="${2:-54000}"; IMAGE="${3:-agentek-litellm:spike}"
docker rm -f "poolspike-$NAME" >/dev/null 2>&1 || true
if ! docker ps --format '{{.Names}}' | grep -q '^poolspike-mock$'; then
  docker rm -f poolspike-mock >/dev/null 2>&1 || true
  docker run -d --name poolspike-mock --network poolspike --memory 128m -p 127.0.0.1:59000:9000 \
    -v "$HERE:/spikes" --entrypoint python "$IMAGE" /spikes/mock_upstream.py >/dev/null
fi
# shellcheck disable=SC2086
docker run -d --name "poolspike-$NAME" --network poolspike --memory 1500m -p "127.0.0.1:$PORT:4000" \
  -v "$HERE:/spikes" ${DEV_MOUNT:+-v "$HERE/../agentek_gateway:/app/agentek_gateway:ro"} \
  -e DATABASE_URL=postgresql://postgres:spike@poolspike-pg:5432/litellm \
  -e LITELLM_MASTER_KEY=sk-spike-master -e LITELLM_SALT_KEY=sk-spike-salt \
  -e REDIS_URL=redis://poolspike-redis:6379 -e STORE_MODEL_IN_DB=True \
  -e SPIKE_DIR=/spikes ${GW_ENV:-} \
  "$IMAGE" --config /spikes/config.yaml --port 4000

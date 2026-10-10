#!/usr/bin/env bash
# Throw-away test rig for the subscription pool: own network, Redis (with an awkward password), Postgres,
# a scripted Codex backend and gateway containers that mount the plugin from a checkout.
#
#   LT_IMAGE=<gateway image> ./rig.sh up
#   ./rig.sh gateway lt-gw-a 4101 [plugin dir]    # repeat with another name and port for a second replica
#   ./rig.sh down
#
# LT_IMAGE must be a gateway image built from the fork; the files the fork changed in LiteLLM itself
# (authenticator, common_request_processing, proxy_server, schema.prisma, the plugin migration) are mounted over it.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FORK="${LT_FORK:-$(cd "$HERE/../.." && pwd)}"
WORK="${LT_DIR:-/tmp/agentek-loadtest}"
NET="${LT_NET:-agentek-lt}"
IMAGE="${LT_IMAGE:?set LT_IMAGE to a gateway image}"
MASTER_KEY="sk-loadtest-master"
REDIS_PASSWORD='a/b@c:d#e'
SITE="/app/.venv/lib/python3.13/site-packages"
MIGRATION="20261009120000_add_agentek_subscription_tables"

certs() {
  mkdir -p "$WORK/certs"
  cd "$WORK/certs"
  openssl req -x509 -newkey rsa:2048 -nodes -keyout ca.key -out ca.crt -days 3 -subj "/CN=agentek-lt-ca" \
    -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -addext "subjectKeyIdentifier=hash" 2>/dev/null
  openssl req -newkey rsa:2048 -nodes -keyout server.key -out server.csr -subj "/CN=chatgpt.com" 2>/dev/null
  printf 'subjectAltName=DNS:chatgpt.com,DNS:auth.openai.com,DNS:mock\nbasicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nauthorityKeyIdentifier=keyid\n' >ext.cnf
  openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out server.crt -days 3 -extfile ext.cnf 2>/dev/null
  cat /etc/ssl/certs/ca-certificates.crt ca.crt >bundle.crt
}

up() {
  docker network create "$NET" >/dev/null 2>&1 || true
  [ -f "$WORK/certs/bundle.crt" ] || certs
  docker rm -f lt-redis lt-pg lt-mock >/dev/null 2>&1 || true
  docker run -d --name lt-redis --network "$NET" --memory 256m redis:7-alpine redis-server --requirepass "$REDIS_PASSWORD" >/dev/null
  docker run -d --name lt-pg --network "$NET" --memory 768m -e POSTGRES_USER=backend -e POSTGRES_PASSWORD=dev \
    -e POSTGRES_DB=litellm_lt postgres:16 >/dev/null
  docker run -d --name lt-mock --network "$NET" --memory 512m \
    --network-alias mock --network-alias chatgpt.com --network-alias auth.openai.com \
    -v "$WORK/certs:/certs:ro" -v "$HERE/mock_server.py:/mock_server.py:ro" \
    -v "$FORK/tests/agentek_gateway:/tests:ro" --entrypoint python "$IMAGE" /mock_server.py >/dev/null
}

gateway() {
  local name="$1" port="$2" plugin="${3:-$FORK/agentek_gateway}"
  docker rm -f "$name" >/dev/null 2>&1 || true
  docker create --name "$name" --network "$NET" --memory 2g --cap-add SYS_PTRACE -p "127.0.0.1:$port:4000" \
    -e LITELLM_MASTER_KEY="$MASTER_KEY" -e LITELLM_SALT_KEY=sk-loadtest-salt \
    -e DATABASE_URL=postgresql://backend:dev@lt-pg:5432/litellm_lt \
    -e STORE_MODEL_IN_DB=True -e LITELLM_DEV_ENABLE_PREMIUM=True \
    -e LITELLM_WORKER_STARTUP_HOOKS=agentek_gateway:startup \
    -e REDIS_HOST=lt-redis -e REDIS_PORT=6379 -e REDIS_DB=2 -e "REDIS_PASSWORD=$REDIS_PASSWORD" \
    -e DEFAULT_MAX_REDIS_BATCH_CACHE_SIZE=10000 \
    -e SSL_CERT_FILE=/etc/ssl/lt/bundle.crt -e REQUESTS_CA_BUNDLE=/etc/ssl/lt/bundle.crt -e LITELLM_LOG=INFO \
    -v "$HERE/config.yaml:/app/config.yaml:ro" -v "$WORK/certs/bundle.crt:/etc/ssl/lt/bundle.crt:ro" \
    -v "$plugin:/app/agentek_gateway:ro" -v "$FORK/schema.prisma:/app/schema.prisma:ro" \
    -v "$FORK/litellm/proxy/schema.prisma:$SITE/litellm/proxy/schema.prisma:ro" \
    -v "$FORK/litellm-proxy-extras/litellm_proxy_extras/schema.prisma:$SITE/litellm_proxy_extras/schema.prisma:ro" \
    -v "$FORK/litellm-proxy-extras/litellm_proxy_extras/migrations/$MIGRATION:$SITE/litellm_proxy_extras/migrations/$MIGRATION:ro" \
    -v "$FORK/litellm/llms/chatgpt/authenticator.py:$SITE/litellm/llms/chatgpt/authenticator.py:ro" \
    -v "$FORK/litellm/llms/chatgpt/common_utils.py:$SITE/litellm/llms/chatgpt/common_utils.py:ro" \
    -v "$FORK/litellm/proxy/common_request_processing.py:$SITE/litellm/proxy/common_request_processing.py:ro" \
    -v "$FORK/litellm/proxy/proxy_server.py:$SITE/litellm/proxy/proxy_server.py:ro" \
    --entrypoint sh "$IMAGE" -c 'cd /app && prisma generate --schema=/app/schema.prisma >/dev/null 2>&1 && exec litellm --config /app/config.yaml --port 4000' >/dev/null
  docker start "$name" >/dev/null
  echo "waiting for $name on :$port"
  until curl -s -m2 -H "Authorization: Bearer $MASTER_KEY" "http://127.0.0.1:$port/agentek/status" | grep -q ready; do sleep 3; done
}

down() {
  docker rm -f lt-redis lt-pg lt-mock >/dev/null 2>&1 || true
  docker ps -aq --filter "network=$NET" | xargs -r docker rm -f >/dev/null
  docker network rm "$NET" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}

"$@"

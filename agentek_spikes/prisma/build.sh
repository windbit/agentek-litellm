#!/usr/bin/env bash
# Builds the 0.7 image variants from the CI image by overlaying schema.prisma (x3) and migrations.
set -euo pipefail
cd "$(dirname "$0")"
BASE=ghcr.io/windbit/agentek-litellm:b649adb72d24cce5b6fd0e06f22fdf9a34e05ac1
SP=/app/.venv/lib/python3.13/site-packages
docker run --rm --entrypoint cat "$BASE" $SP/litellm_proxy_extras/schema.prisma > base.prisma
# BEGIN/END markers: the block is appended after the last upstream model
cat base.prisma block_v1.prisma > v1.prisma
python3 - <<'PY'
v1 = open("v1.prisma").read()
open("v2.prisma", "w").write(v1.replace("// END agentek", open("block_v2_policy.prisma").read().strip("\n") + "\n// END agentek"))
open("base_dummy.prisma", "w").write(open("base.prisma").read() + open("dummy.prisma").read())
open("v1_dummy.prisma", "w").write(v1 + open("dummy.prisma").read())
PY
diffsql() { docker run --rm -v "$PWD:/p" --entrypoint prisma "$BASE" migrate diff --from-schema-datamodel /p/$1 --to-schema-datamodel /p/$2 --script 2>/dev/null; }
mkdir -p mig/20261001000000_agentek_block mig/20261002000000_agentek_policy mig/20261003000000_spike_dummy
diffsql base.prisma v1.prisma > mig/20261001000000_agentek_block/migration.sql
diffsql v1.prisma v2.prisma > mig/20261002000000_agentek_policy/migration.sql
diffsql base.prisma base_dummy.prisma > mig/20261003000000_spike_dummy/migration.sql
wc -l mig/*/migration.sql
variant() { # name schema migrations...
  local name=$1 schema=$2; shift 2
  rm -rf ctx && mkdir -p ctx/migs && cp "$schema" ctx/schema.prisma
  for m in "$@"; do cp -r "mig/$m" ctx/migs/; done
  cat > ctx/Dockerfile <<DF
FROM $BASE
COPY schema.prisma /app/schema.prisma
COPY schema.prisma $SP/litellm/proxy/schema.prisma
COPY schema.prisma $SP/litellm_proxy_extras/schema.prisma
COPY migs/ $SP/litellm_proxy_extras/migrations/
DF
  docker build -q -t "agentek-litellm:$name" ctx >/dev/null && echo "built $name"
}
variant p-v1       v1.prisma         20261001000000_agentek_block
variant p-v2       v2.prisma         20261001000000_agentek_block 20261002000000_agentek_policy
variant p-nb-pend  base_dummy.prisma 20261003000000_spike_dummy
variant p-v1-pend  v1_dummy.prisma   20261001000000_agentek_block 20261003000000_spike_dummy
rm -rf ctx

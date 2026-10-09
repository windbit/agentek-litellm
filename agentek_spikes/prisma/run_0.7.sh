#!/usr/bin/env bash
# 0.7 scenarios. Each one uses a fresh database in the spike Postgres. Output -> logs/0.7_run.txt
set -uo pipefail
cd "$(dirname "$0")"
PSQL="docker exec -i poolspike-pg psql -U postgres -At"
mig() { # db image [V2]
  docker run --rm --network poolspike -e V2="${3:-0}" -e DATABASE_URL="postgresql://postgres:spike@poolspike-pg:5432/$1" \
    -v "$PWD/run_migrate.py:/run_migrate.py:ro" --entrypoint python "$2" /run_migrate.py 2>&1 \
    | grep -E "RESULT|prisma migrate deploy completed|No pending|Generating migration diff|DIFF_SQL|^DROP|^ALTER|^CREATE|Migration diff applied|Failed|ERROR|The following migration|Applying migration|applied" | cut -c1-200
}
newdb() { $PSQL -c "drop database if exists $1" -c "create database $1" >/dev/null; }
tables() { $PSQL -d "$1" -c "select string_agg(tablename, ',' order by tablename) from pg_tables where tablename like 'LiteLLM_Agentek%' or tablename='LiteLLM_SpikeDummy'"; }
rows() { $PSQL -d "$1" -c "select count(*) from \"$2\""; }
echo "##### prisma CLI in the image"; docker run --rm --entrypoint prisma agentek-litellm:p-v1 --version 2>/dev/null | head -3

echo; echo "##### S1 fresh DB, image WITH block (p-v1): migrate deploy applies upstream + agentek migration"
newdb s1; mig s1 agentek-litellm:p-v1; echo "tables: $(tables s1)"
$PSQL -d s1 -c "insert into \"LiteLLM_AgentekSubscription\"(id,provider,name,credential_name,updated_at) values ('x1','chatgpt','sub-1','cred-1',now())" >/dev/null
echo "-- second start, same image:"; mig s1 agentek-litellm:p-v1; echo "rows after: $(rows s1 LiteLLM_AgentekSubscription)"

echo; echo "##### S2 rollback: DB with block+row, start image WITHOUT block and WITHOUT pending migration (plain old image)"
mig s1 ghcr.io/windbit/agentek-litellm:b649adb72d24cce5b6fd0e06f22fdf9a34e05ac1; echo "tables: $(tables s1) rows: $(rows s1 LiteLLM_AgentekSubscription)"

echo; echo "##### S3 DB with block+row, image WITHOUT block but with one PENDING migration (e.g. next upstream bump without the block) -> v1 resolver"
newdb s3; mig s3 agentek-litellm:p-v1 >/dev/null
$PSQL -d s3 -c "insert into \"LiteLLM_AgentekSubscription\"(id,provider,name,credential_name,updated_at) values ('x1','chatgpt','sub-1','cred-1',now())" >/dev/null
mig s3 agentek-litellm:p-nb-pend; echo "tables: $(tables s3) rows: $(rows s3 LiteLLM_AgentekSubscription 2>&1 | head -1)"

echo; echo "##### S3v2 same, v2 resolver"
newdb s3b; mig s3b agentek-litellm:p-v1 >/dev/null
$PSQL -d s3b -c "insert into \"LiteLLM_AgentekSubscription\"(id,provider,name,credential_name,updated_at) values ('x1','chatgpt','sub-1','cred-1',now())" >/dev/null
mig s3b agentek-litellm:p-nb-pend 1; echo "tables: $(tables s3b) rows: $(rows s3b LiteLLM_AgentekSubscription 2>&1 | head -1)"

echo; echo "##### S4 DB with block+row, image WITH block and one pending migration -> drift check must not propose DROP"
newdb s4; mig s4 agentek-litellm:p-v1 >/dev/null
$PSQL -d s4 -c "insert into \"LiteLLM_AgentekSubscription\"(id,provider,name,credential_name,updated_at) values ('x1','chatgpt','sub-1','cred-1',now())" >/dev/null
mig s4 agentek-litellm:p-v1-pend; echo "tables: $(tables s4) rows: $(rows s4 LiteLLM_AgentekSubscription 2>&1 | head -1)"

echo; echo "##### S5 rollback p-v2 -> image WITH block v1 but WITHOUT the newer migration (policy table), with a pending migration forcing the drift check"
newdb s5; mig s5 agentek-litellm:p-v2 >/dev/null
$PSQL -d s5 -c "insert into \"LiteLLM_AgentekSubscription\"(id,provider,name,credential_name,updated_at) values ('x1','chatgpt','sub-1','cred-1',now())" -c "insert into \"LiteLLM_AgentekSubscriptionPolicy\"(id,subject,visibility,updated_at) values ('p1','employee:u1','all',now())" >/dev/null
echo "before: $(tables s5)"
mig s5 agentek-litellm:p-v1-pend; echo "after: $(tables s5); subscription rows: $(rows s5 LiteLLM_AgentekSubscription) policy rows: $(rows s5 LiteLLM_AgentekSubscriptionPolicy 2>&1 | head -1)"

echo; echo "##### S5b same rollback without any pending migration (plain rollback p-v2 -> p-v1)"
newdb s5b; mig s5b agentek-litellm:p-v2 >/dev/null
mig s5b agentek-litellm:p-v1; echo "after: $(tables s5b)"

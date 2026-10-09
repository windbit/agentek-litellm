#!/usr/bin/env bash
# 0.7 addendum: the v2 resolver (--use_v2_migration_resolver) against the S5 rollback case (DB ahead by one migration + pending migration).
set -uo pipefail
cd "$(dirname "$0")"
PSQL="docker exec -i poolspike-pg psql -U postgres -At"
mig() { docker run --rm --network poolspike -e V2="${3:-0}" -e DATABASE_URL="postgresql://postgres:spike@poolspike-pg:5432/$1" -v "$PWD/run_migrate.py:/run_migrate.py:ro" --entrypoint python "$2" /run_migrate.py 2>&1 | grep -E "RESULT|No pending|DIFF_SQL|^DROP|^ALTER|Migration diff applied|Failed|ERROR|v2 migration resolver" | cut -c1-200; }
tables() { $PSQL -d "$1" -c "select string_agg(tablename, ',' order by tablename) from pg_tables where tablename like 'LiteLLM_Agentek%' or tablename='LiteLLM_SpikeDummy'"; }
echo "##### S5-v2: p-v2 DB (policy table + row), rollback to p-v1-pend with the v2 resolver"
$PSQL -c "drop database if exists s5v2" -c "create database s5v2" >/dev/null
mig s5v2 agentek-litellm:p-v2 >/dev/null
$PSQL -d s5v2 -c "insert into \"LiteLLM_AgentekSubscriptionPolicy\"(id,subject,visibility,updated_at) values ('p1','employee:u1','all',now())" >/dev/null
echo "before: $(tables s5v2)"
mig s5v2 agentek-litellm:p-v1-pend 1
echo "after: $(tables s5v2); policy rows: $($PSQL -d s5v2 -c 'select count(*) from "LiteLLM_AgentekSubscriptionPolicy"' 2>&1 | head -1)"

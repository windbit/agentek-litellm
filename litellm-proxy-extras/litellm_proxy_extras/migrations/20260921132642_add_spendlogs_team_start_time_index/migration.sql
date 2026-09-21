-- CreateIndex
-- INCLUDE covers the per-consumer aggregate (agentek-console organization-accounts, part F): index-only
-- scan answers GROUP BY "user"/end_user/api_key without a heap fetch. Prisma's DSL can't express INCLUDE,
-- so this column list is hand-added after `run_migration.py` generated the base (team_id, startTime) index.
CREATE INDEX "LiteLLM_SpendLogs_team_id_startTime_idx" ON "LiteLLM_SpendLogs"("team_id", "startTime")
  INCLUDE (spend, "user", end_user, api_key);


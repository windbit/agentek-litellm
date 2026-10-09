-- CreateTable
CREATE TABLE "LiteLLM_AgentekSubscription" (
    "id" TEXT NOT NULL,
    "provider" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "credential_name" TEXT NOT NULL,
    "priority" INTEGER NOT NULL DEFAULT 50,
    "enabled" BOOLEAN NOT NULL DEFAULT true,
    "max_concurrency" INTEGER,
    "egress" TEXT,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "LiteLLM_AgentekSubscription_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "LiteLLM_AgentekSubscriptionState" (
    "subscription_id" TEXT NOT NULL,
    "state" TEXT NOT NULL,
    "until" TIMESTAMP(3),
    "reason" TEXT,
    "source" TEXT,
    "overloaded_hits" INTEGER NOT NULL DEFAULT 0,
    "version" INTEGER NOT NULL DEFAULT 0,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "LiteLLM_AgentekSubscriptionState_pkey" PRIMARY KEY ("subscription_id")
);

-- CreateTable
CREATE TABLE "LiteLLM_AgentekSubscriptionPolicy" (
    "subscription_id" TEXT NOT NULL,
    "visibility" TEXT NOT NULL DEFAULT 'all',
    "visibility_subjects" TEXT[],
    "bound_subjects" TEXT[],
    "version" INTEGER NOT NULL DEFAULT 0,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "LiteLLM_AgentekSubscriptionPolicy_pkey" PRIMARY KEY ("subscription_id")
);

-- CreateTable
CREATE TABLE "LiteLLM_AgentekAudit" (
    "id" TEXT NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "actor" TEXT NOT NULL,
    "action" TEXT NOT NULL,
    "subscription_id" TEXT,
    "subscription_name" TEXT,
    "subject" TEXT,
    "before" JSONB,
    "after" JSONB,

    CONSTRAINT "LiteLLM_AgentekAudit_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "LiteLLM_AgentekSubscriptionDailyStat" (
    "subscription_id" TEXT NOT NULL,
    "day" DATE NOT NULL,
    "failures" JSONB NOT NULL DEFAULT '{}',
    "switches" JSONB NOT NULL DEFAULT '{}',
    "state_seconds" JSONB NOT NULL DEFAULT '{}',
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "LiteLLM_AgentekSubscriptionDailyStat_pkey" PRIMARY KEY ("subscription_id","day")
);

-- CreateIndex
CREATE UNIQUE INDEX "LiteLLM_AgentekSubscription_name_key" ON "LiteLLM_AgentekSubscription"("name");

-- CreateIndex
CREATE INDEX "LiteLLM_AgentekAudit_created_at_idx" ON "LiteLLM_AgentekAudit"("created_at");

-- CreateIndex
CREATE INDEX "LiteLLM_AgentekAudit_subscription_id_created_at_idx" ON "LiteLLM_AgentekAudit"("subscription_id", "created_at");

-- AddForeignKey
ALTER TABLE "LiteLLM_AgentekSubscriptionState" ADD CONSTRAINT "LiteLLM_AgentekSubscriptionState_subscription_id_fkey" FOREIGN KEY ("subscription_id") REFERENCES "LiteLLM_AgentekSubscription"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "LiteLLM_AgentekSubscriptionPolicy" ADD CONSTRAINT "LiteLLM_AgentekSubscriptionPolicy_subscription_id_fkey" FOREIGN KEY ("subscription_id") REFERENCES "LiteLLM_AgentekSubscription"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "LiteLLM_AgentekSubscriptionDailyStat" ADD CONSTRAINT "LiteLLM_AgentekSubscriptionDailyStat_subscription_id_fkey" FOREIGN KEY ("subscription_id") REFERENCES "LiteLLM_AgentekSubscription"("id") ON DELETE CASCADE ON UPDATE CASCADE;


-- CreateTable
CREATE TABLE "LiteLLM_AgentekSubscription" (
    "id" TEXT NOT NULL,
    "provider" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "credential_name" TEXT NOT NULL,
    "priority" INTEGER NOT NULL DEFAULT 100,
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
    "version" INTEGER NOT NULL DEFAULT 0,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "LiteLLM_AgentekSubscriptionState_pkey" PRIMARY KEY ("subscription_id")
);

-- CreateIndex
CREATE UNIQUE INDEX "LiteLLM_AgentekSubscription_name_key" ON "LiteLLM_AgentekSubscription"("name");

-- AddForeignKey
ALTER TABLE "LiteLLM_AgentekSubscriptionState" ADD CONSTRAINT "LiteLLM_AgentekSubscriptionState_subscription_id_fkey" FOREIGN KEY ("subscription_id") REFERENCES "LiteLLM_AgentekSubscription"("id") ON DELETE CASCADE ON UPDATE CASCADE;


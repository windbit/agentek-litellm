-- CreateTable
CREATE TABLE "LiteLLM_AgentekSubscriptionPolicy" (
    "id" TEXT NOT NULL,
    "subject" TEXT NOT NULL,
    "visibility" TEXT NOT NULL,
    "version" INTEGER NOT NULL DEFAULT 0,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "LiteLLM_AgentekSubscriptionPolicy_pkey" PRIMARY KEY ("id")
);


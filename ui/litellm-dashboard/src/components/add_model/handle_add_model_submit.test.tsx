import { describe, expect, it, vi } from "vitest";
import { prepareModelAddRequest } from "./handle_add_model_submit";

vi.mock("../molecules/notifications_manager", () => ({
  default: {
    fromBackend: vi.fn(),
  },
}));

describe("prepareModelAddRequest", () => {
  it("returns deployment data for the most basic form", async () => {
    const formValues = {
      model_mappings: [
        {
          public_name: "Public Model",
          litellm_model: "litellm/public",
        },
      ],
      model_name: "custom-model-name",
      base_model: "gpt-4",
      team_id: "team-123",
      model_access_group: ["group-1"],
      input_cost_per_token: "2000000",
      output_cost_per_token: "1000000",
    };

    const deployments = await prepareModelAddRequest({ ...formValues }, "token", null);

    expect(deployments).toHaveLength(1);
    const [deployment] = deployments!;
    expect(deployment.modelName).toBe("Public Model");
    expect(deployment.litellmParamsObj.model).toBe("custom-model-name");
    expect(deployment.litellmParamsObj.input_cost_per_token).toBe(2);
    expect(deployment.litellmParamsObj.output_cost_per_token).toBe(1);
    expect(deployment.modelInfoObj.base_model).toBe("gpt-4");
    expect(deployment.modelInfoObj.access_groups).toEqual(["group-1"]);
    expect(deployment.modelInfoObj.team_id).toBe("team-123");
  });

  it("uses a lowercase fallback for unrecognized custom providers", async () => {
    const fallbackValues = {
      model_mappings: [
        {
          public_name: "Petals Model",
          litellm_model: "petals/model",
        },
      ],
      model_name: "petals/model",
      custom_llm_provider: "Petals",
    };

    const deployments = await prepareModelAddRequest({ ...fallbackValues }, "token", null);

    expect(deployments).toHaveLength(1);
    const [deployment] = deployments!;
    expect(deployment.litellmParamsObj.custom_llm_provider).toBe("petals");
  });

  it("ignores litellm_credential_name inside LiteLLM Params JSON", async () => {
    const formValues = {
      model_mappings: [
        {
          public_name: "Public Model",
          litellm_model: "litellm/public",
        },
      ],
      model_name: "custom-model-name",
      litellm_credential_name: "selected-credential",
      litellm_extra_params: JSON.stringify({
        litellm_credential_name: "from-json",
        timeout: 5,
      }),
    };

    const deployments = await prepareModelAddRequest({ ...formValues }, "token", null);

    expect(deployments).toHaveLength(1);
    const [deployment] = deployments!;
    expect(deployment.litellmParamsObj.litellm_credential_name).toBe("selected-credential");
    expect(deployment.litellmParamsObj.timeout).toBe(5);
  });

  it("sends litellm_credential_name and no inline secrets when an existing ChatGPT credential is selected", async () => {
    // When "Existing Credentials" is chosen the AddModelForm hides the
    // ProviderSpecificFields, so inline token fields are never part of formValues.
    // The deployment must reference the credential by name and carry no secrets.
    const formValues = {
      model_mappings: [
        {
          public_name: "chatgpt/gpt-5.3-codex",
          litellm_model: "chatgpt/gpt-5.3-codex",
        },
      ],
      model_name: "chatgpt/gpt-5.3-codex",
      custom_llm_provider: "ChatGPT",
      litellm_credential_name: "chatgpt-account-a",
    };

    const deployments = await prepareModelAddRequest({ ...formValues }, "token", null);

    expect(deployments).toHaveLength(1);
    const [deployment] = deployments!;
    expect(deployment.litellmParamsObj.litellm_credential_name).toBe("chatgpt-account-a");
    expect(deployment.litellmParamsObj.custom_llm_provider).toBe("chatgpt");
    // No inline credential secrets should be present.
    expect(deployment.litellmParamsObj.access_token).toBeUndefined();
    expect(deployment.litellmParamsObj.refresh_token).toBeUndefined();
    expect(deployment.litellmParamsObj.id_token).toBeUndefined();
  });

  it("forwards inline ChatGPT tokens only when manual credential fields are used", async () => {
    // The complementary case: no existing credential selected, so the manual
    // ProviderSpecificFields values flow through into litellm_params.
    const formValues = {
      model_mappings: [
        {
          public_name: "chatgpt/gpt-5.3-codex",
          litellm_model: "chatgpt/gpt-5.3-codex",
        },
      ],
      model_name: "chatgpt/gpt-5.3-codex",
      custom_llm_provider: "ChatGPT",
      access_token: "inline-access-token",
      refresh_token: "inline-refresh-token",
    };

    const deployments = await prepareModelAddRequest({ ...formValues }, "token", null);

    expect(deployments).toHaveLength(1);
    const [deployment] = deployments!;
    expect(deployment.litellmParamsObj.custom_llm_provider).toBe("chatgpt");
    expect(deployment.litellmParamsObj.access_token).toBe("inline-access-token");
    expect(deployment.litellmParamsObj.refresh_token).toBe("inline-refresh-token");
    expect(deployment.litellmParamsObj.litellm_credential_name).toBeUndefined();
  });
});

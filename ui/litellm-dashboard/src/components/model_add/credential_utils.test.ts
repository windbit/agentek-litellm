import { describe, expect, it } from "vitest";
import { buildCredentialPayload, credentialProviderToEnumKey, resolveCredentialProvider } from "./credential_utils";

describe("resolveCredentialProvider", () => {
  it("should map the ChatGPT provider to the backend slug", () => {
    // The backend resolves ChatGPT per-deployment credentials by
    // custom_llm_provider === "chatgpt", so the dropdown's "ChatGPT" enum value
    // must be stored as the slug.
    expect(resolveCredentialProvider("ChatGPT")).toBe("chatgpt");
  });

  it("should preserve existing behavior for other providers", () => {
    expect(resolveCredentialProvider("OpenAI")).toBe("OpenAI");
    expect(resolveCredentialProvider("Anthropic")).toBe("Anthropic");
    expect(resolveCredentialProvider("Azure")).toBe("Azure");
  });
});

describe("credentialProviderToEnumKey", () => {
  it("should map the ChatGPT slug back to the enum key for editing", () => {
    expect(credentialProviderToEnumKey("chatgpt")).toBe("ChatGPT");
  });

  it("should pass through values stored by other providers unchanged", () => {
    expect(credentialProviderToEnumKey("OpenAI")).toBe("OpenAI");
    expect(credentialProviderToEnumKey("anthropic")).toBe("anthropic");
  });

  it("should round-trip ChatGPT through resolve + reverse", () => {
    expect(credentialProviderToEnumKey(resolveCredentialProvider("ChatGPT"))).toBe("ChatGPT");
  });
});

describe("buildCredentialPayload", () => {
  it("should store the ChatGPT credential with custom_llm_provider 'chatgpt' and values under credential_values", () => {
    const payload = buildCredentialPayload({
      credential_name: "chatgpt-account-a",
      custom_llm_provider: "ChatGPT",
      chatgpt_auth_file: "/secrets/chatgpt/account-a/auth.json",
    });

    expect(payload.credential_name).toBe("chatgpt-account-a");
    expect(payload.credential_info.custom_llm_provider).toBe("chatgpt");

    // Credential selection is carried in credential_values, never in credential_info.
    expect(payload.credential_values).toEqual({
      chatgpt_auth_file: "/secrets/chatgpt/account-a/auth.json",
    });
    // The provider/name descriptors must not leak into credential_values.
    expect(payload.credential_values).not.toHaveProperty("custom_llm_provider");
    expect(payload.credential_values).not.toHaveProperty("credential_name");
  });

  it("should preserve the provider value for non-ChatGPT credentials", () => {
    const payload = buildCredentialPayload({
      credential_name: "openai-prod",
      custom_llm_provider: "OpenAI",
      api_key: "sk-123",
    });

    expect(payload.credential_info.custom_llm_provider).toBe("OpenAI");
    expect(payload.credential_values).toEqual({ api_key: "sk-123" });
  });
});

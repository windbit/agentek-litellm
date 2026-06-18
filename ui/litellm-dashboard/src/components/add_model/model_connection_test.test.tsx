import { describe, expect, it } from "vitest";
import { formatCurlCommand, maskHeaderValue, sanitizeCurlHeaders } from "./model_connection_test";

// formatCurlCommand re-masks credential-bearing headers so a raw Authorization /
// ChatGPT-Account-Id never reaches the rendered curl or clipboard.

describe("maskHeaderValue", () => {
  it("should mask the Authorization header value", () => {
    expect(maskHeaderValue("Authorization", "Bearer secret-access-token-123456")).not.toContain("secret-access-token");
  });

  it("should mask the ChatGPT-Account-Id header value", () => {
    const masked = maskHeaderValue("ChatGPT-Account-Id", "acct-secret-id-123456");
    expect(masked).not.toContain("acct-secret-id");
    expect(masked).not.toEqual("acct-secret-id-123456");
  });

  it("should leave non-sensitive header values untouched", () => {
    expect(maskHeaderValue("Content-Type", "application/json")).toEqual("application/json");
  });

  it("should fully mask short sensitive values", () => {
    expect(maskHeaderValue("api-key", "abc")).toEqual("*****");
  });
});

describe("sanitizeCurlHeaders", () => {
  it("should mask every credential-bearing header while preserving others", () => {
    const sanitized = sanitizeCurlHeaders({
      Authorization: "Bearer secret-access-token-123456",
      "ChatGPT-Account-Id": "acct-secret-id-123456",
      "Content-Type": "application/json",
    });

    expect(sanitized["Authorization"]).not.toContain("secret-access-token");
    expect(sanitized["ChatGPT-Account-Id"]).not.toContain("acct-secret-id");
    expect(sanitized["Content-Type"]).toEqual("application/json");
  });
});

describe("formatCurlCommand", () => {
  it("should never render raw Authorization or ChatGPT-Account-Id values in the curl command", () => {
    const curl = formatCurlCommand(
      "https://chatgpt.com/backend-api/codex/responses",
      { model: "gpt-5", messages: [{ role: "user", content: "hi" }] },
      {
        Authorization: "Bearer secret-access-token-123456",
        "ChatGPT-Account-Id": "acct-secret-id-123456",
      },
    );

    expect(curl).not.toContain("secret-access-token-123456");
    expect(curl).not.toContain("acct-secret-id-123456");
    // The header names are still present (only the values are masked).
    expect(curl).toContain("ChatGPT-Account-Id:");
    expect(curl).toContain("Authorization:");
  });
});

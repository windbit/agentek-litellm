import { CredentialItem } from "../networking";
import { Providers, provider_map } from "../provider_info_helpers";

// Fields that describe the credential itself rather than its secret values.
// They must never be copied into credential_values.
const RESTRICTED_CREDENTIAL_FIELDS = ["credential_name", "custom_llm_provider"];

// ChatGPT per-deployment credentials are resolved by `custom_llm_provider === "chatgpt"`,
// so the stored credential must use the litellm slug, not the dropdown enum value.
// Other providers pass through unchanged.
const PROVIDERS_REQUIRING_SLUG: ReadonlySet<string> = new Set<string>([Providers.ChatGPT]);

// Map providers the backend matches by slug (currently only ChatGPT) to their
// litellm slug; others pass through unchanged.
export const resolveCredentialProvider = (provider: string): string => {
  if (PROVIDERS_REQUIRING_SLUG.has(provider)) {
    return provider_map[provider] ?? provider;
  }
  return provider;
};

// Inverse of resolveCredentialProvider: map a stored slug back to the UI enum key
// for the edit dropdown; non-slug values pass through unchanged.
export const credentialProviderToEnumKey = (stored: string): string => {
  for (const enumKey of PROVIDERS_REQUIRING_SLUG) {
    if (provider_map[enumKey] === stored) {
      return enumKey;
    }
  }
  return stored;
};

// Build the credential payload (secret inputs → credential_values, resolved slug →
// credential_info). Shared by Add and Edit so the two stay in sync.
export const buildCredentialPayload = (values: Record<string, any>): CredentialItem => {
  const credentialValues = Object.entries(values)
    .filter(([key]) => !RESTRICTED_CREDENTIAL_FIELDS.includes(key))
    .reduce((acc, [key, value]) => ({ ...acc, [key]: value }), {} as Record<string, any>);

  return {
    credential_name: values.credential_name,
    credential_values: credentialValues,
    credential_info: {
      custom_llm_provider: resolveCredentialProvider(values.custom_llm_provider),
    },
  };
};

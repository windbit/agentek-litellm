# Gateway environment variables added by this fork

## DISABLE_CHATGPT_CREDENTIAL_REFRESH

Set to `true` (case-insensitive) to skip registering the built-in `refresh_chatgpt_credentials_job`
at startup. Any other value, or no value, keeps the job. The job only runs when `store_model_in_db`
is enabled. Read once at startup, so changing it needs a restart.

## AGENTEK_GATEWAY_CONFIG

JSON for the subscription pool plugin (`agentek_gateway`). The `defaults` object overrides the built-in tuning for every provider and `providers.<id>` overrides it for one provider (for example `{"providers": {"chatgpt": {"concurrency_limit": 4, "probe_model": "gpt-5.4"}}}`). Unknown keys fail startup. Read once at startup.

## AGENTEK_GATEWAY_REDIS_URL

Redis the plugin keeps subscription state, slots, chat bindings and the leader lease in. Falls back to `REDIS_URL`, then to `REDIS_HOST`, `REDIS_PORT` and `REDIS_PASSWORD`.

## AGENTEK_GATEWAY_REDIS_PREFIX

Key prefix of everything the plugin writes to Redis, `agentek:` by default. Set a distinct prefix when environments share one Redis.

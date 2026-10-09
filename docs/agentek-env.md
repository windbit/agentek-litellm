# Gateway environment variables added by this fork

## DISABLE_CHATGPT_CREDENTIAL_REFRESH

Set to `true` (case-insensitive) to skip registering the built-in `refresh_chatgpt_credentials_job`
at startup. Any other value, or no value, keeps the job. The job only runs when `store_model_in_db`
is enabled. Read once at startup, so changing it needs a restart.

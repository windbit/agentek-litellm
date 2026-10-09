\echo '== window 7d: failures by class/code'
select coalesce(metadata->'error_information'->>'error_class','-') cls, coalesce(metadata->'error_information'->>'error_code','-') code, count(*) n
from "LiteLLM_SpendLogs" where "startTime" > now() - interval '7 days' and status <> 'success' and exists (select 1 from jsonb_array_elements_text(request_tags) x where x like 'Credential: chatgpt%') group by 1,2 order by 3 desc limit 10;
\echo '== per credential 7d: total, failed(non-budget)'
with t as (select status, metadata->'error_information'->>'error_class' cls, (select x from jsonb_array_elements_text(request_tags) x where x like 'Credential: %' limit 1) cred from "LiteLLM_SpendLogs" where "startTime" > now() - interval '7 days' and jsonb_typeof(request_tags)='array')
select cred, count(*) total, count(*) filter (where status<>'success' and coalesce(cls,'')<>'BudgetExceededError') failed from t where cred like 'Credential: chatgpt%' group by 1 order by 2 desc;
\echo '== 2-minute bins: active chatgpt creds vs creds with a failure (only bins with >=1 failure)'
with t as (select date_bin('2 minutes', "startTime", timestamp '2026-01-01') b, status, metadata->'error_information'->>'error_class' cls, (select x from jsonb_array_elements_text(request_tags) x where x like 'Credential: chatgpt%' limit 1) cred from "LiteLLM_SpendLogs" where "startTime" > now() - interval '7 days' and jsonb_typeof(request_tags)='array'),
b as (select b, count(distinct cred) active, count(distinct cred) filter (where status<>'success' and coalesce(cls,'')<>'BudgetExceededError') failing, count(*) filter (where status<>'success' and coalesce(cls,'')<>'BudgetExceededError') fails from t where cred is not null group by b)
select failing||'/'||active as failing_of_active, count(*) bins, sum(fails) failures from b where failing>0 group by 1 order by 2 desc;

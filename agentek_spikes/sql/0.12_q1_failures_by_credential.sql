\echo '== failures by error_class (24h, aggregate)'
select coalesce(metadata->'error_information'->>'error_class','-') cls, coalesce(metadata->'error_information'->>'error_code','-') code, count(*) n
from "LiteLLM_SpendLogs" where "startTime" > now() - interval '24 hours' and status <> 'success' group by 1,2 order by 3 desc limit 12;
\echo '== requests by credential tag (24h): total, failures'
with t as (select status, (select x from jsonb_array_elements_text(request_tags) x where x like 'Credential: %' limit 1) cred from "LiteLLM_SpendLogs" where "startTime" > now() - interval '24 hours' and jsonb_typeof(request_tags)='array')
select coalesce(cred,'(none)') cred, count(*) total, count(*) filter (where status<>'success') failed from t group by 1 order by 2 desc limit 20;

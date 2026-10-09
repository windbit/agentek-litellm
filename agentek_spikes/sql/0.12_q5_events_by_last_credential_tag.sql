\echo '== events: 2-minute bin | credential (last Credential tag = deployment of the final attempt) | errors'
select to_char(date_bin('2 minutes', "startTime", timestamp '2026-01-01'), 'YYYY-MM-DD HH24:MI'), cred, count(*) from (
  select "startTime", (select x from jsonb_array_elements_text(request_tags) with ordinality t(x,i) where x like 'Credential: chatgpt%' order by i desc limit 1) cred
  from "LiteLLM_SpendLogs" where "startTime" > now() - interval '7 days' and status <> 'success' and jsonb_typeof(request_tags)='array'
   and coalesce(metadata->'error_information'->>'error_class','') not in ('RateLimitError','BudgetExceededError','BadRequestError')) q
where cred is not null group by 1,2 order by 1,2;

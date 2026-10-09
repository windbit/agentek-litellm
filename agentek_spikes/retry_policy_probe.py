"""0.9: how to get 4 retries on the Responses path: num_retries, retry_policy, model_group_retry_policy, per-deployment num_retries.
Runs inside the spike image against the mock (always 429 usage_limit). Counts upstream attempts per request."""
import asyncio, json, time, urllib.request

import litellm
from litellm import Router
from litellm.types.router import RetryPolicy

MOCK = "http://poolspike-mock:9000"


def ctl(path, body):
    req = urllib.request.Request(MOCK + path, data=json.dumps(body).encode(), headers={"content-type": "application/json"})
    return urllib.request.urlopen(req).read()


def attempts():
    return len([l for l in open("/spikes/logs/mock_upstream.jsonl") if l.strip()])


def deployments(group, count, extra=None):
    out = []
    for idx in range(count):
        params = {"model": "chatgpt/gpt-5.4", "chatgpt_api_base": MOCK,
                  "chatgpt_auth": {"access_token": "x", "expires_at": 4102444800, "account_id": f"acct{'ABC'[idx]}"}}
        params.update(extra or {})
        out.append({"model_name": group, "litellm_params": params, "model_info": {"id": f"{group}-{idx}", "mode": "responses"}})
    return out


async def run_case(label, count, router_kwargs, dep_extra=None, group="grp"):
    ctl("/_ctl/reset", {})
    for acct in "ABC":
        ctl("/_ctl/script", {"account": f"acct{acct}", "script": [], "default": "usage_limit"})
    router = Router(model_list=deployments(group, count, dep_extra), **router_kwargs)
    t0 = time.time()
    try:
        await router.aresponses(model=group, input="hi", stream=False)
        outcome = "ok"
    except Exception as err:
        outcome = type(err).__name__
    print(json.dumps({"case": label, "deployments": count, "upstream_attempts": attempts(), "outcome": outcome, "elapsed_s": round(time.time() - t0, 1)}), flush=True)


async def main():
    policy = RetryPolicy(RateLimitErrorRetries=4)
    for count in (1, 3):
        await run_case("default (no num_retries)", count, {})
        await run_case("num_retries=4", count, {"num_retries": 4})
        await run_case("retry_policy RateLimitErrorRetries=4", count, {"retry_policy": policy})
        await run_case("model_group_retry_policy {grp: RateLimitErrorRetries=4}", count, {"model_group_retry_policy": {"grp": policy}})
        await run_case("per-deployment litellm_params.num_retries=4", count, {}, dep_extra={"num_retries": 4})
        await run_case("per-deployment litellm_params.max_retries=4", count, {}, dep_extra={"max_retries": 4})


asyncio.run(main())

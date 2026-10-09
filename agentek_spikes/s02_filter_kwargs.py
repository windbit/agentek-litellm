"""0.1: what async_filter_deployments sees for Responses (Hermes-like, prompt_cache_key) and chat, incl. retry / failover."""
import json, time
from lib import *
LOG = "logs/spike_events.jsonl"
key = open("logs/key.txt").read()

def run(label, fn, scripts):
    mock_reset()
    for acct, script in scripts.items():
        mock_ctl(acct, script=script, default="ok")
    n0 = len(read_jsonl(LOG))
    st, hdr, body = fn()
    time.sleep(1.5)
    ev = read_jsonl(LOG)[n0:]
    print(f"\n=== {label}: status={st} body[:160]={str(body)[:160]!r}")
    for e in ev:
        if e["event"] in ("filter", "pre_call_hook", "failure", "success", "proxy_failure_hook"):
            print(json.dumps({k: v for k, v in e.items() if k not in ("t", "pid")}, default=str)[:1500])
    return ev

pck = "pck-hermes-chat-1"
run("responses ok", lambda: responses_req(key, prompt_cache_key=pck), {})
run("chat ok", lambda: chat_req(key, prompt_cache_key=pck), {})
run("responses first deployment 429 -> retry/failover", lambda: responses_req(key, prompt_cache_key=pck),
    {"acctA": ["usage_limit"], "acctB": ["usage_limit"], "acctC": ["usage_limit"]})

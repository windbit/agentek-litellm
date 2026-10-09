"""0.2 (mock variant): 429 usage_limit and response.failed in SSE, successful stream — what reaches exception / hooks / client."""
import json, sys, time
import os
from lib import *
key = open(os.path.join(LOGS, "key.txt")).read()
MOCKLOG = os.path.join(LOGS, "mock_upstream.jsonl")

def scenario(label, fn, script, acct="acctA"):
    settle(); mock_reset(); mark(label)
    mock_ctl(acct, script=script, default="ok")
    t0 = time.time(); st, hdr, body = fn(); dt = time.time() - t0; time.sleep(1.2)
    interesting = {k: v for k, v in hdr.items() if any(x in k.lower() for x in ("codex", "retry", "x-litellm-attempted", "x-litellm-model-id"))}
    print(f"\n=== {label}: HTTP {st} in {dt:.1f}s; attempts at upstream={len(read_jsonl(MOCKLOG))}")
    print("  client headers:", interesting)
    print("  client body   :", str(body)[:420].replace("\n", " | "))
    for e in events_since_mark(label):
        if e["event"] in ("failure", "success", "proxy_failure_hook"):
            if e["event"] == "failure":
                print("  " + brief(e)); print("     exc_dump:", json.dumps(e.get("exc_dump"))[:900])
            elif e["event"] == "success":
                print(f"  success stream={e['stream']} codex_headers={sorted(k.replace('llm_provider-','') for k in e['codex_headers'])[:3]}... n={len(e['codex_headers'])}")
            else:
                print("  " + brief(e))

scenario("0.2 A responses non-stream usage_limit", lambda: responses_req(key, model="solo"), ["usage_limit"] * 5)
scenario("0.2 B responses stream usage_limit", lambda: responses_req(key, model="solo", stream=True), ["usage_limit"] * 5)
scenario("0.2 C responses non-stream SSE response.failed", lambda: responses_req(key, model="solo"), ["sse_failed"] * 5)
scenario("0.2 D responses stream SSE response.failed", lambda: responses_req(key, model="solo", stream=True), ["sse_failed"] * 5)
scenario("0.2 E responses stream ok", lambda: responses_req(key, model="solo", stream=True), [])
scenario("0.2 F chat non-stream usage_limit", lambda: chat_req(key, model="solo"), ["usage_limit"] * 5)
scenario("0.2 G chat stream sse response.failed", lambda: chat_req(key, model="solo", stream=True), ["sse_failed"] * 5)

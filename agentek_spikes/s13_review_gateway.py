"""Review follow-up checks that need the gateway: 0.5 raw evidence, request-id keys (X2), own final error over HTTP (R3), all-cooled group (R4)."""
import json, os, sys, time, urllib.request
from lib import *

OUT = []
def say(line):
    print(line, flush=True); OUT.append(line)

def setup():
    for name, acct in (("sub-a", "acctA"), ("sub-b", "acctB"), ("sub-c", "acctC")):
        add_subscription(name, acct)
    add_priced("priced", ["sub-a", "sub-b", "sub-c"])
    key = new_key("review-key", metadata={"labels": ["employee:u1"]})
    open(os.path.join(LOGS, "key.txt"), "w").write(key)

def raw(path, key=None):
    req = urllib.request.Request(GW + path, headers={"authorization": f"Bearer {key}"} if key else {})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()

def check_05():
    say("## 0.5 raw checks (gateway container built from the CI image + COPY agentek_gateway)")
    say(f"GET /agentek/spike/ping -> {raw('/agentek/spike/ping')}")
    say(f"GET /agentek/spike/whoami (no key) -> {raw('/agentek/spike/whoami')[0]}")
    say(f"GET /agentek/spike/whoami (master key) -> {raw('/agentek/spike/whoami', MASTER)}")
    st, body = raw("/metrics/", MASTER)
    say(f"GET /metrics/ (master key) -> {st}; agentek lines: {[l for l in body.splitlines() if l.startswith('agentek_spike') and not l.startswith('#')][:3]}")
    say(f"GET /metrics (no slash) -> {raw('/metrics', MASTER)[0]} (redirect to /metrics/)")
    for e in read_jsonl(EVENTS):
        if e["event"] in ("startup_hook_begin", "ready"):
            say("startup event: " + json.dumps({k: v for k, v in e.items() if k not in ("pid",)}))
            break
    ready = [e for e in read_jsonl(EVENTS) if e["event"] == "ready"][-1]
    say("last ready event: " + json.dumps(ready))

def x2(key):
    say("## X2 client-chosen litellm_call_id vs server-issued request id")
    write_ctl(filter_mode="log")
    mark("X2")
    req = urllib.request.Request(GW + "/v1/responses", data=json.dumps({"model": "gpt-5.4", "input": "hi", "stream": False}).encode(),
                                 headers={"content-type": "application/json", "authorization": f"Bearer {key}", "x-litellm-call-id": "client-chosen-call-id"})
    urllib.request.urlopen(req, timeout=30).read(); time.sleep(1)
    for e in events_since_mark("X2"):
        if e["event"] == "filter":
            say(f"filter saw litellm_call_id={e['call_id']!r} agentek_request_id={e['agentek_request_id']!r}")

def r3_http(key):
    say("## R3 own error over HTTP: plugin allows only A, A fails with 503")
    for cls in ("retryable", "final"):
        write_ctl(filter_mode="pick_order", pick_order=["priced:sub-a"], track_attempts=True, own_error_class=cls)
        settle(); mock_reset(); mock_ctl("acctA", script=["overloaded"] * 8, default="ok")
        t0 = time.time(); st, h, b = responses_req(key, model="priced"); dt = time.time() - t0
        say(f"own error class={cls}: HTTP {st} in {dt:.1f}s Retry-After={h.get('retry-after')} upstream attempts={len(read_jsonl(os.path.join(LOGS, 'mock_upstream.jsonl')))}")

def r4_http(key):
    say("## R4 all deployments of the group in router cooldown (3 x upstream 429), then new requests; plugin raises on an empty list")
    write_ctl(filter_mode="log", raise_on_empty=True, own_error_class="final")
    settle(); mock_reset()
    for a in ("acctA", "acctB", "acctC"):
        mock_ctl(a, script=[], default="usage_limit")
    for n in range(5):
        t0 = time.time(); st, h, b = responses_req(key, model="priced"); dt = time.time() - t0
        say(f"request {n+1}: HTTP {st} in {dt:.1f}s Retry-After={h.get('retry-after')} upstream so far={len(read_jsonl(os.path.join(LOGS, 'mock_upstream.jsonl')))} body={str(b)[:90]!r}")
    write_ctl(filter_mode="log")

if __name__ == "__main__":
    if "--setup" in sys.argv:
        setup()
    key = open(os.path.join(LOGS, "key.txt")).read()
    check_05(); x2(key); r3_http(key); r4_http(key)
    open(os.path.join(LOGS, "review_gateway_checks.txt"), "w").write("\n".join(OUT) + "\n")

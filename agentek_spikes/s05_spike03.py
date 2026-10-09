"""0.3: which errors the plugin hooks see: 2 errors in a row in one non-stream request, error on 3rd stream chunk, client disconnect."""
import collections, http.client, json, time
import os
from lib import *
key = open(os.path.join(LOGS, "key.txt")).read()
MOCKLOG = os.path.join(LOGS, "mock_upstream.jsonl")

def summarize(label, extra=""):
    evs = [e for e in events_since_mark(label) if e["event"] != "MARK"]
    cnt = collections.Counter(e["event"] for e in evs)
    up = read_jsonl(MOCKLOG)
    print(f"  upstream attempts={len(up)} ({[u['account'][-1]+':'+u['action'] for u in up]})  hook events={dict(cnt)} {extra}")
    for e in evs:
        if e["event"] in ("failure", "provider_error", "proxy_failure_hook", "success"):
            d = {k: e.get(k) for k in ("status", "deployment_id", "exc_class", "stream", "message", "text") if e.get(k) is not None}
            print("    ", e["event"], json.dumps(d)[:230])

def scenario(label, fn, scripts, note=""):
    settle(); mock_reset(); mark(label)
    for acct, sc in scripts.items():
        mock_ctl(acct, script=sc, default="ok")
    t0 = time.time(); st, h, b = fn(); dt = time.time() - t0; time.sleep(1.5)
    print(f"\n=== {label}: HTTP {st} {dt:.1f}s body={str(b)[:90]!r} {note}")
    summarize(label)

abc = lambda sc: {"acctA": sc, "acctB": sc, "acctC": sc}
scenario("0.3 a non-stream, group of 3, 503 x all", lambda: responses_req(key, model="gpt-5.4"), abc(["overloaded"] * 6))
scenario("0.3 b non-stream, solo, 503 x3", lambda: responses_req(key, model="solo"), {"acctA": ["overloaded"] * 6})
scenario("0.3 c stream responses, solo, error event at chunk 3", lambda: responses_req(key, model="solo", stream=True), {"acctA": ["midstream_error"] * 6})
scenario("0.3 d stream responses, solo, connection abort at chunk 3", lambda: responses_req(key, model="solo", stream=True), {"acctA": ["midstream_abort"] * 6})
scenario("0.3 e stream chat, solo, error event at chunk 3", lambda: chat_req(key, model="solo", stream=True), {"acctA": ["midstream_error"] * 6})

# f: client disconnect
label = "0.3 f client disconnect after 2 chunks (slow stream)"
settle(); mock_reset(); mark(label); mock_ctl("acctA", script=["slow_stream"], default="ok")
conn = http.client.HTTPConnection("127.0.0.1", 54000, timeout=30)
conn.request("POST", "/v1/responses", json.dumps({"model": "solo", "input": "hi", "stream": True}), {"content-type": "application/json", "authorization": f"Bearer {key}"})
resp = conn.getresponse(); got = 0
while got < 3:
    line = resp.fp.readline()
    if line.startswith(b"data:"): got += 1
conn.close()
print(f"\n=== {label}: closed client after {got} data lines")
time.sleep(10)
summarize(label)

"""0.4: upstream 400 "model not supported when using Codex with a ChatGPT account" on subscription A: does the router retry on B?
The router picks A first only ~1/3 of the time, so each variant repeats until A is hit twice."""
import json, time
import os
from lib import *
key = open(os.path.join(LOGS, "key.txt")).read()
MOCKLOG = os.path.join(LOGS, "mock_upstream.jsonl")

def write_ctl(**kw):
    json.dump(kw, open(os.path.join(HERE, "ctl.json"), "w")); time.sleep(0.3)

def attempt(label, stream):
    settle(); mock_reset(); mark(label)
    mock_ctl("acctA", script=["model_not_supported"] * 5, default="ok")
    fn = responses_req
    st, h, b = fn(key, model="gpt-5.4", stream=stream); time.sleep(1.2)
    up = [u["account"][-1] + ":" + u["action"] for u in read_jsonl(MOCKLOG)]
    return st, up, str(b)[:150].replace("\n", " "), [e for e in events_since_mark(label) if e["event"] in ("failure", "provider_error", "provider_error_remapped", "proxy_failure_hook")]

for variant in (None, 403, 409, 503, 404):
    write_ctl(filter_mode="log", **({"remap_not_supported_to": variant} if variant else {}))
    hits = 0; tries = 0
    while hits < 2 and tries < 10:
        tries += 1
        label = f"0.4 remap={variant} try{tries}"
        st, up, body, evs = attempt(label, stream=True)
        if up and up[0].startswith("A"):
            hits += 1
            kinds = [(e["event"], e.get("status") or e.get("to")) for e in evs]
            print(f"remap={variant}: HTTP {st} upstream={up} events={kinds} body={body[:100]!r}")
write_ctl(filter_mode="log")

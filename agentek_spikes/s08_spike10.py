"""0.10: plugin filter returns ONE deployment per attempt + weighted failover; check _excluded_deployment_ids handling and the no-candidates error."""
import json, time
from lib import *
MOCKLOG = "logs/mock_upstream.jsonl"
A, B, C = "priced:sub-a", "priced:sub-b", "priced:sub-c"
key = new_key("spike-10")

def scenario(label, order, scripts, stream, track=False):
    write_ctl(filter_mode="pick_order", pick_order=order, track_attempts=track)
    settle(); mock_reset(); mark(label)
    for acct, sc in scripts.items():
        mock_ctl(acct, script=sc, default="ok")
    t0 = time.time(); st, h, b = responses_req(key, model="priced", stream=stream); dt = time.time() - t0; time.sleep(1.5)
    up = [u["account"][-1] + ":" + u["action"] for u in read_jsonl(MOCKLOG)]
    hdr = {k: v for k, v in h.items() if k.lower() in ("retry-after", "x-litellm-attempted-retries", "x-litellm-attempted-fallbacks")}
    print(f"\n=== {label}: stream={stream} HTTP {st} in {dt:.1f}s upstream={up} headers={hdr}")
    print("    body:", str(b)[:200].replace("\n", " "))
    for e in events_since_mark(label):
        if e["event"] == "filter":
            print(f"    filter excl={e['excluded']} meta_excl={e['failover_excluded_meta']} healthy={[i.split(':')[-1] for i in e['healthy_ids']]}")
        elif e["event"] == "filter_pick":
            print(f"    pick -> {e['picked']}")
        elif e["event"] == "failure":
            print(f"    failure hook: {e.get('exc_class')} {e.get('status')} dep={e.get('deployment_id')}")

lim = ["usage_limit"] * 6
scenario("0.10 a stream: A 429 pre-chunk, plugin allows A,B,C", [A, B, C], {"acctA": lim}, True)
scenario("0.10 b stream: A and B 429, plugin allows only A,B", [A, B], {"acctA": lim, "acctB": lim}, True)
scenario("0.10 c stream: A,B,C all 429, plugin allows all", [A, B, C], {"acctA": lim, "acctB": lim, "acctC": lim}, True)
scenario("0.10 d non-stream: A 429, plugin allows A,B,C, no own tracking", [A, B, C], {"acctA": lim}, False, track=False)
scenario("0.10 e non-stream: A 429, plugin allows A,B,C, own per-call tracking", [A, B, C], {"acctA": lim}, False, track=True)
scenario("0.10 f non-stream: A,B 429, plugin allows A,B, own tracking", [A, B], {"acctA": lim, "acctB": lim}, False, track=True)
write_ctl(filter_mode="log")

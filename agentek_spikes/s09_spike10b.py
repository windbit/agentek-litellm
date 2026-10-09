"""0.10 (cont.): (1) real weighted failover (stream dies before the first chunk) -> does the filter see _excluded_deployment_ids;
(2) how our own "no subscriptions" error carries Retry-After to the client."""
import json, time
from lib import *
MOCKLOG = "logs/mock_upstream.jsonl"
A, B, C = "priced:sub-a", "priced:sub-b", "priced:sub-c"
key = new_key("spike-10b")

def scenario(label, order, scripts, stream, **ctl_kw):
    write_ctl(filter_mode="pick_order", pick_order=order, **ctl_kw)
    settle(); mock_reset(); mark(label)
    for acct, sc in scripts.items():
        mock_ctl(acct, script=sc, default="ok")
    t0 = time.time(); st, h, b = responses_req(key, model="priced", stream=stream); dt = time.time() - t0; time.sleep(1.5)
    up = [u["account"][-1] + ":" + u["action"] for u in read_jsonl(MOCKLOG)]
    hdr = {k: v for k, v in h.items() if k.lower() in ("retry-after",)}
    print(f"\n=== {label}: stream={stream} HTTP {st} in {dt:.1f}s upstream={up} client Retry-After={hdr}")
    print("    body:", str(b)[:140].replace("\n", " "))
    for e in events_since_mark(label):
        if e["event"] == "filter":
            print(f"    filter excl={e['excluded']} meta_excl={e['failover_excluded_meta']} healthy={[i.split(':')[-1] for i in e['healthy_ids']]}")
        elif e["event"] == "filter_pick":
            print(f"    pick -> {e['picked']}")
        elif e["event"] == "failure":
            print(f"    failure hook: {e.get('exc_class')} {e.get('status')} dep={e.get('deployment_id')} text={str(e.get('exc_text'))[:60]!r}")

cah = ["close_after_headers"] * 6
scenario("0.10 g real failover: A dies before first chunk, plugin allows A,B,C", [A, B, C], {"acctA": cah}, True)
scenario("0.10 h real failover: A and B die before first chunk, plugin allows A,B only", [A, B], {"acctA": cah, "acctB": cah}, True)
lim = ["usage_limit"] * 6
for variant in ("response", "attr", "hook"):
    scenario(f"0.10 i retry-after variant={variant}: A,B exhausted, plugin allows A,B", [A, B], {"acctA": lim, "acctB": lim}, False, track_attempts=True, retry_after_variant=variant)
write_ctl(filter_mode="log")

"""0.10 (cont.): where does the router actually emit _excluded_deployment_ids? chat vs Responses, with an unclassified 500 on A.
The plugin does NOT track attempts here, so A is re-picked until the router's own failover kicks in."""
import time
import os
from lib import *
MOCKLOG = os.path.join(LOGS, "mock_upstream.jsonl")
A, B = "priced:sub-a", "priced:sub-b"
key = new_key("spike-10c")
def scenario(label, fn):
    write_ctl(filter_mode="pick_order", pick_order=[A, B], track_attempts=False)
    settle(); mock_reset(); mark(label); mock_ctl("acctA", script=["internal"] * 8, default="ok")
    t0 = time.time(); st, h, b = fn(); dt = time.time() - t0; time.sleep(1.5)
    up = [u["account"][-1] + ":" + u["action"] for u in read_jsonl(MOCKLOG)]
    print(f"\n=== {label}: HTTP {st} in {dt:.1f}s upstream={up}")
    for e in events_since_mark(label):
        if e["event"] == "filter":
            print(f"    filter excl={e['excluded']} meta_excl={e['failover_excluded_meta']}")
scenario("0.10 j chat non-stream", lambda: chat_req(key, model="priced"))
scenario("0.10 k chat stream", lambda: chat_req(key, model="priced", stream=True))
scenario("0.10 l responses non-stream", lambda: responses_req(key, model="priced"))
scenario("0.10 m responses stream", lambda: responses_req(key, model="priced", stream=True))
write_ctl(filter_mode="log")

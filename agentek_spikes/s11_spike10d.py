"""0.10 (cont.): chat path, plugin allows ONLY A, A fails 3x -> router weighted failover wants B/C (plugin rejects them): final error, timing, Retry-After."""
import time
from lib import *
MOCKLOG = "logs/mock_upstream.jsonl"
A = "priced:sub-a"
key = new_key("spike-10d")
for stream in (False, True):
    label = f"0.10 n chat stream={stream}: plugin allows only A, A fails"
    write_ctl(filter_mode="pick_order", pick_order=[A], track_attempts=False, retry_after_variant="attr")
    settle(); mock_reset(); mark(label); mock_ctl("acctA", script=["internal"] * 8, default="ok")
    t0 = time.time(); st, h, b = chat_req(key, model="priced", stream=stream); dt = time.time() - t0; time.sleep(1.5)
    up = [u["account"][-1] + ":" + u["action"] for u in read_jsonl(MOCKLOG)]
    print(f"\n=== {label}: HTTP {st} in {dt:.1f}s upstream={up} Retry-After={h.get('retry-after')}")
    print("    body:", str(b)[:200].replace("\n", " "))
    for e in events_since_mark(label):
        if e["event"] == "filter":
            print(f"    filter excl={e['excluded']}")
        elif e["event"] == "filter_pick":
            print(f"    pick -> {e['picked']}")
write_ctl(filter_mode="log")

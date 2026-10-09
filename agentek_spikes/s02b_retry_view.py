"""0.1 (continued): compact view of retry/failover attempts for Responses + chat."""
import time
import os
from lib import *
key = open(os.path.join(LOGS, "key.txt")).read()
def scenario(label, fn, scripts):
    mock_reset(); mark(label)
    for acct, script in scripts.items():
        mock_ctl(acct, script=script, default="ok")
    st, hdr, body = fn(); time.sleep(1.5)
    print(f"\n=== {label}: HTTP {st}")
    for e in events_since_mark(label):
        if e["event"] != "MARK": print("  " + brief(e))
pck = "pck-hermes-chat-1"
scenario("0.1 responses: 429 on first pick", lambda: responses_req(key, prompt_cache_key=pck), {"acctA": ["usage_limit"], "acctB": ["usage_limit"], "acctC": ["usage_limit"]})
scenario("0.1 chat: 429 on first pick", lambda: chat_req(key, prompt_cache_key=pck), {"acctA": ["usage_limit"], "acctB": ["usage_limit"], "acctC": ["usage_limit"]})
scenario("0.1 responses: 429, 429, then ok", lambda: responses_req(key, prompt_cache_key=pck), {"acctA": ["usage_limit", "usage_limit"], "acctB": ["usage_limit", "usage_limit"], "acctC": ["usage_limit", "usage_limit"]})

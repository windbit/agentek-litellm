"""0.8 (tag rewrite after A->B switch) and 0.10 (filter + weighted failover) on one gateway with a priced model group."""
import json, sys, time
from lib import *
MOCKLOG = "logs/mock_upstream.jsonl"
A, B, C = "priced:sub-a", "priced:sub-b", "priced:sub-c"

def setup():
    if "--setup" in sys.argv:
        print(add_priced("priced", ["sub-a", "sub-b", "sub-c"]))

def tag_totals():
    rows = psql("select tag, coalesce(sum(spend),0), coalesce(sum(api_requests),0), coalesce(sum(successful_requests),0), coalesce(sum(failed_requests),0) from \"LiteLLM_DailyTagSpend\" where tag like 'Credential:%' group by tag order by tag")
    out = {}
    for line in rows.splitlines():
        tag, spend, req, ok, bad = line.split("|")
        out[tag] = (float(spend), int(req), int(ok), int(bad))
    return out

def settle_db():
    time.sleep(75)  # spend queue is flushed to Postgres in batches

def run_switch(mode):
    label = f"0.8 rewrite_tags={mode}"
    write_ctl(filter_mode="pick_order", pick_order=[A, B], rewrite_tags=mode)
    key = new_key(f"spike-8-{mode}", metadata={"tags": ["team-x"]})
    settle(); mock_reset(); mark(label)
    mock_ctl("acctA", script=["usage_limit"], default="ok")
    before = tag_totals()
    st, h, b = responses_req(key, model="priced", stream=True)
    time.sleep(2)
    evs = events_since_mark(label)
    succ = [e for e in evs if e["event"] == "success"]
    up = [u["account"][-1] + ":" + u["action"] for u in read_jsonl(MOCKLOG)]
    print(f"\n=== {label}: HTTP {st} upstream={up}")
    for e in evs:
        if e["event"] in ("pre_call_hook",):
            print("   pre_call dep=%s tags_after=%s" % (e["deployment_id"], e["tags_after"]))
    for e in succ:
        print("   success dep=%s request_tags=%s" % (e["deployment_id"], e["request_tags"]))
    return label, before

def finish(label, before):
    after = tag_totals()
    print(f"   DailyTagSpend delta ({label}):")
    for tag in sorted(set(before) | set(after)):
        b0 = before.get(tag, (0, 0, 0, 0)); a0 = after.get(tag, (0, 0, 0, 0))
        d = tuple(round(x - y, 6) for x, y in zip(a0, b0))
        if any(d): print(f"     {tag:22s} spend={d[0]} api_requests={d[1]} ok={d[2]} failed={d[3]}")
    last = psql("select request_tags, model_id, spend from \"LiteLLM_SpendLogs\" where model_group='priced' order by \"startTime\" desc limit 1")
    print("   last spend log row (request_tags|model_id|spend):", last)

if __name__ == "__main__":
    setup()
    runs = []
    for mode in ("", "assign", "inplace"):
        runs.append(run_switch(mode or None))
        settle_db()
        finish(*runs[-1])

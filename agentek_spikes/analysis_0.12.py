"""0.12: is the coincidence of unclassified errors across subscriptions of one egress above chance?
Input: logs/0.12_box_events.csv (2-minute bin, anonymised credential, error count; box cluster, 7 days).
Two null models, 5000 draws each:
  A. credential labels of error events are permuted WITHIN the hour (keeps when errors happen, tests who suffers)
  B. each credential's whole series is shifted by one random offset (keeps its own bursts, removes alignment between credentials)
usage: python3 analysis_0.12.py [events.csv]
"""
import collections, csv, datetime as dt, json, os, random, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "logs", "0.12_box_events.csv")
DRAWS = 5000
rows = [(dt.datetime.strptime(r["bin_2min_utc"], "%Y-%m-%d %H:%M"), r["credential"], int(r["errors"])) for r in csv.DictReader(open(PATH))]
t0 = min(r[0] for r in rows).replace(hour=0, minute=0)
SPAN = 7 * 24 * 30
creds = sorted({r[1] for r in rows})
events = [(int((t - t0).total_seconds() // 120), c) for t, c, n in rows for _ in range(n)]


def stats(ev):
    per_bin = collections.defaultdict(set)
    for b, c in ev:
        per_bin[b].add(c)
    k = collections.Counter(len(v) for v in per_bin.values())
    return {"bins_with_errors": len(per_bin), "exactly_1": k[1], "ge2": sum(v for x, v in k.items() if x >= 2),
            "ge3": sum(v for x, v in k.items() if x >= 3), "ge4": sum(v for x, v in k.items() if x >= 4)}


def summarize(draws, observed):
    out = {}
    for key in ("ge2", "ge3", "ge4"):
        arr = sorted(d[key] for d in draws)
        out[key] = {"observed": observed[key], "null_mean": round(sum(arr) / len(arr), 2), "null_p95": arr[int(len(arr) * .95)],
                    "null_p99": arr[int(len(arr) * .99)], "null_max": arr[-1], "p_value": round(sum(v >= observed[key] for v in arr) / len(arr), 4)}
    return out


random.seed(11)
observed = stats(events)
by_hour = collections.defaultdict(list)
for i, (b, _) in enumerate(events):
    by_hour[b // 30].append(i)
draws_a = []
for _ in range(DRAWS):
    labels = [c for _, c in events]
    for idx in by_hour.values():
        shuffled = [events[i][1] for i in idx]
        random.shuffle(shuffled)
        for i, c in zip(idx, shuffled):
            labels[i] = c
    draws_a.append(stats([(events[i][0], labels[i]) for i in range(len(events))]))
series = {c: [(b, c) for b, cc in events if cc == c] for c in creds}
draws_b = []
for _ in range(DRAWS):
    shifted = []
    for c, ev in series.items():
        off = random.randrange(SPAN)
        shifted += [((b + off) % SPAN, c) for b, _ in ev]
    draws_b.append(stats(shifted))
result = {"credentials": len(creds), "errors": len(events), "observed": observed,
          "null_A_permute_within_hour": summarize(draws_a, observed), "null_B_shift_per_credential": summarize(draws_b, observed)}
print(json.dumps(result, indent=1))
json.dump(result, open(os.path.join(HERE, "logs", "0.12_box_permutation.json"), "w"), indent=1)

"""Tiny stdlib helpers shared by spike scripts (run with python3 -I from the host)."""
import json
import os
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(HERE, "logs")
GW = os.environ.get("GW", "http://127.0.0.1:54000")
MOCK = os.environ.get("MOCK", "http://127.0.0.1:59000")
MASTER = "sk-spike-master"
MOCK_INTERNAL = "http://poolspike-mock:9000"


def call(method, url, body=None, key=MASTER, timeout=60, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"content-type": "application/json", "authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode()
            return resp.status, dict(resp.headers), (text if raw else _j(text))
    except urllib.error.HTTPError as err:
        text = err.read().decode()
        return err.code, dict(err.headers), (text if raw else _j(text))


def _j(text):
    try:
        return json.loads(text)
    except ValueError:
        return text


def mock_ctl(account, **kw):
    return call("POST", f"{MOCK}/_ctl/script", {"account": account, **kw}, key="x")


def mock_reset():
    return call("POST", f"{MOCK}/_ctl/reset", {}, key="x")


def add_subscription(name, account, models=("gpt-5.4",), api_base=MOCK_INTERNAL, mode="responses", gw=GW, extra_params=None):
    auth = {"access_token": "fake.jwt.token", "refresh_token": "rt-fake", "expires_at": 4102444800, "account_id": account}
    st, _, body = call("POST", f"{gw}/credentials", {"credential_name": name, "credential_info": {"custom_llm_provider": "chatgpt"}, "credential_values": {"chatgpt_auth": auth, "chatgpt_api_base": api_base}})
    assert st == 200, (st, body)
    ids = {}
    for model in models:
        params = {"model": f"chatgpt/{model}", "litellm_credential_name": name}
        params.update(extra_params or {})
        st, _, body = call("POST", f"{gw}/model/new", {"model_name": model, "litellm_params": params, "model_info": {"id": f"sub:{name}:{model}", "mode": mode}})
        assert st == 200, (st, body)
        ids[model] = f"sub:{name}:{model}"
    return ids


def new_key(alias, metadata=None, gw=GW, **extra):
    import time
    st, _, body = call("POST", f"{gw}/key/generate", {"key_alias": f"{alias}-{int(time.time())}", "metadata": metadata or {}, **extra})
    assert st == 200, (st, body)
    return body["key"]


def responses_req(key, text="hi", stream=False, gw=GW, model="gpt-5.4", **extra):
    body = {"model": model, "input": text, "stream": stream, **extra}
    return call("POST", f"{gw}/v1/responses", body, key=key, raw=True)


def chat_req(key, text="hi", stream=False, gw=GW, model="gpt-5.4", **extra):
    body = {"model": model, "messages": [{"role": "user", "content": text}], "stream": stream, **extra}
    return call("POST", f"{gw}/v1/chat/completions", body, key=key, raw=True)


def read_jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path) as handle:
        return [json.loads(line) for line in handle if line.strip()]


EVENTS = os.path.join(LOGS, "spike_events.jsonl")


def mark(label):
    import time
    with open(EVENTS, "a") as handle:
        handle.write(json.dumps({"t": round(time.time(), 3), "event": "MARK", "label": label}) + "\n")


def events_since_mark(label):
    evs = read_jsonl(EVENTS)
    idx = max(i for i, e in enumerate(evs) if e.get("event") == "MARK" and e.get("label") == label)
    return evs[idx + 1:]


def brief(e):
    ev = e["event"]
    cid = (e.get("call_id") or "")[:6]
    if ev == "filter":
        return f"filter  call={cid} rk={str(e['rk_id'])[-5:]} excl={e['excluded']} target_order={e['target_order']} meta_excl={e['failover_excluded_meta']} pck_paths={e['prompt_cache_key_paths']} healthy={[i.split(':')[1] for i in e['healthy_ids']]}"
    if ev == "pre_call_hook":
        return f"pre_call call={cid} {e['call_type']} dep={e['deployment_id']} tags_before={e['tags_before']} tags_after={e['tags_after']}"
    if ev in ("failure", "success"):
        extra = {k: e.get(k) for k in ("status", "exc_class", "exc_headers", "exc_body", "stream", "elapsed", "request_tags") if e.get(k) is not None}
        return f"{ev:8s} call={cid} dep={e.get('deployment_id')} {extra}"
    return f"{ev} " + json.dumps({k: v for k, v in e.items() if k not in ('t', 'pid', 'event')}, default=str)[:300]


def settle(seconds=6.5):
    """Clear Redis (router cooldown cache lives there) and wait out the in-memory 5 s cooldown."""
    import subprocess, time
    subprocess.run(["docker", "exec", "poolspike-redis", "redis-cli", "FLUSHALL"], check=True, capture_output=True)
    time.sleep(seconds)


def add_solo(model_name, credential, upstream="gpt-5.4", dep_id=None, gw=GW):
    st, _, body = call("POST", f"{gw}/model/new", {"model_name": model_name, "litellm_params": {"model": f"chatgpt/{upstream}", "litellm_credential_name": credential}, "model_info": {"id": dep_id or f"solo:{credential}:{model_name}", "mode": "responses"}})
    assert st == 200, (st, body)


def add_priced(model_name, subs, price_in=0.001, price_out=0.002, gw=GW):
    ids = []
    for sub in subs:
        dep = f"{model_name}:{sub}"
        st, _, body = call("POST", f"{gw}/model/new", {"model_name": model_name, "litellm_params": {"model": "chatgpt/gpt-5.4", "litellm_credential_name": sub, "input_cost_per_token": price_in, "output_cost_per_token": price_out}, "model_info": {"id": dep, "mode": "responses"}})
        assert st == 200, (st, body)
        ids.append(dep)
    return ids


def psql(sql, db="litellm"):
    import subprocess
    out = subprocess.run(["docker", "exec", "poolspike-pg", "psql", "-U", "postgres", "-d", db, "-At", "-F", "|", "-c", sql], capture_output=True, text=True)
    return out.stdout.strip()


def write_ctl(**kw):
    import time
    json.dump(kw, open(os.path.join(HERE, "ctl.json"), "w"))
    time.sleep(0.4)

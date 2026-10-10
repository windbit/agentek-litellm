# ruff: noqa: T201
"""Experiments against a rig started by rig.sh (host side, standard library only).

python3 experiments.py setup 12            # 12 subscriptions on the scripted backend
python3 experiments.py dead 10 40 300      # 10 of 12 dead, concurrency 40, 300 requests: client 429s, hits on dead accounts
python3 experiments.py cold 10 8 31        # first request after a reset, 8 trials 31 s apart
python3 experiments.py flush                # FLUSHDB of the plugin database in the middle of the load
python3 experiments.py calls chat 300       # Redis commands per request
python3 experiments.py healthy 40 600       # throughput with every subscription alive
python3 experiments.py usage                # limit windows after chat requests
python3 experiments.py stop gw-name         # docker stop -t 30 under load with dead accounts
"""

import base64
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGE = os.environ["LT_IMAGE"]
NET = os.environ.get("LT_NET", "agentek-lt")
GATEWAY_NAME = os.environ.get("LT_GATEWAY", "lt-gw-a")
GATEWAY_PORT = int(os.environ.get("LT_PORT", "4101"))
MASTER_KEY = "sk-loadtest-master"
REDIS_PASSWORD = "a/b@c:d#e"
PLUGIN_DB = "2"
MODEL = "gpt-5.4"
TOKEN_LIFETIME_S = 3 * 24 * 3600


def http(method, url, body=None, timeout=30):
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={
            "Authorization": f"Bearer {MASTER_KEY}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            return reply.status, dict(reply.headers), reply.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read().decode()


def gateway(path, body=None, method=None):
    return http(
        method or ("POST" if body is not None else "GET"),
        f"http://127.0.0.1:{GATEWAY_PORT}{path}",
        body,
    )


def chat_once():
    return gateway(
        "/v1/chat/completions",
        {"model": MODEL, "messages": [{"role": "user", "content": "hi"}]},
    )


def sql(query):
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            "lt-pg",
            "psql",
            "-U",
            "backend",
            "-d",
            "litellm_lt",
            "-tA",
            "-c",
            query,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def redis(*args):
    command = [
        "docker",
        "exec",
        "lt-redis",
        "redis-cli",
        "-a",
        REDIS_PASSWORD,
        "--no-auth-warning",
        "-n",
        PLUGIN_DB,
        *args,
    ]
    return subprocess.run(command, capture_output=True, text=True).stdout.strip()


def mock_address():
    template = '{{(index .NetworkSettings.Networks "' + NET + '").IPAddress}}'
    return subprocess.run(
        ["docker", "inspect", "lt-mock", "-f", template], capture_output=True, text=True
    ).stdout.strip()


def mock(control):
    return http("POST", f"http://{mock_address()}:8000/ctl", control)


def mock_log():
    return json.loads(http("GET", f"http://{mock_address()}:8000/log")[2])


def jwt(claims):
    def encode(part):
        return base64.urlsafe_b64encode(json.dumps(part).encode()).rstrip(b"=").decode()

    return f"{encode({'alg': 'none'})}.{encode(claims)}.sig"


def auth_for(index):
    account = f"acct-s{index:02d}"
    expires = int(time.time()) + TOKEN_LIFETIME_S
    claims = {
        "exp": expires,
        "https://api.openai.com/auth": {"chatgpt_account_id": account},
    }
    return {
        "access_token": jwt(claims),
        "refresh_token": f"{account}|rt0",
        "id_token": jwt(claims),
        "expires_at": expires,
        "account_id": account,
    }


def reset(settle_s=3.0):
    mock({"reset": True})
    sql('delete from "LiteLLM_AgentekSubscriptionState"')
    redis("flushdb")
    sql('update "LiteLLM_AgentekSubscription" set enabled=true')
    time.sleep(settle_s)


def dead_accounts(count):
    return {f"acct-s{index:02d}": "usage_limit" for index in range(1, count + 1)}


def drive(config):
    config = {"urls": [f"http://{GATEWAY_NAME}:4000"], **config}
    command = [
        "docker",
        "run",
        "--rm",
        "--network",
        NET,
        "--memory",
        "1g",
        "--entrypoint",
        "python",
        "-v",
        f"{HERE}:/t:ro",
        IMAGE,
        "/t/driver.py",
        json.dumps(config),
    ]
    output = subprocess.run(command, capture_output=True, text=True)
    try:
        return json.loads(output.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return {"error": output.stdout + output.stderr}


def setup(count):
    for index in range(1, count + 1):
        sid = f"s{index:02d}"
        name = f"cred-{sid}"
        gateway(
            "/credentials",
            {
                "credential_name": name,
                "credential_values": {"chatgpt_auth": auth_for(index)},
                "credential_info": {},
            },
        )
        gateway(
            "/model/new",
            {
                "model_name": MODEL,
                "litellm_params": {
                    "model": f"chatgpt/{MODEL}",
                    "chatgpt_api_base": "http://mock:8000",
                    "litellm_credential_name": name,
                },
                "model_info": {
                    "id": f"sub:{sid}:{MODEL}",
                    "mode": "responses",
                    "input_cost_per_token": 0.00001,
                    "output_cost_per_token": 0.00004,
                },
            },
        )
        sql(
            f"""INSERT INTO "LiteLLM_AgentekSubscription"(id,provider,name,credential_name,priority,enabled,updated_at)
                VALUES ('{sid}','chatgpt','Sub {sid}','{name}',50,true,now()) ON CONFLICT DO NOTHING"""
        )
    print(sql('select count(*) from "LiteLLM_AgentekSubscription"'), "subscriptions")


def dead(count, concurrency, requests):
    reset()
    mock({"defaults": dead_accounts(count)})
    result = drive({"n": requests, "conc": concurrency})
    hits = sum(1 for _, _, action in mock_log() if action == "usage_limit")
    print(
        {
            "status": result.get("status"),
            "p99_ms": result.get("p99_ms"),
            "rps": result.get("rps"),
            "dead_hits": hits,
        }
    )


def cold(count, trials, wait_s):
    failures = 0
    for trial in range(trials):
        reset()
        mock({"defaults": dead_accounts(count)})
        status = chat_once()[0]
        failures += status != 200
        print("trial", trial, status, flush=True)
        time.sleep(wait_s)
    print(
        f"cold first request, {count} of 12 dead: client errors {failures} of {trials}"
    )


def flush():
    reset()
    mock({"defaults": dead_accounts(10)})
    result = {}
    thread = threading.Thread(
        target=lambda: result.update(drive({"n": 600, "conc": 40}))
    )
    thread.start()
    time.sleep(15)
    redis("flushdb")
    thread.join()
    hits = sum(1 for _, _, action in mock_log() if action == "usage_limit")
    print(
        {
            "status": result.get("status"),
            "p99_ms": result.get("p99_ms"),
            "dead_hits": hits,
        }
    )


def command_counts():
    output = subprocess.run(
        [
            "docker",
            "exec",
            "lt-redis",
            "redis-cli",
            "-a",
            REDIS_PASSWORD,
            "--no-auth-warning",
            "info",
            "commandstats",
        ],
        capture_output=True,
        text=True,
    ).stdout
    return {
        name: int(calls)
        for name, calls in re.findall(r"cmdstat_(\w+):calls=(\d+)", output)
    }


def calls(api, requests):
    reset()
    drive({"n": 30, "conc": 4, "api": api})
    before = command_counts()
    config = {"n": requests, "conc": 10, "api": api}
    if api == "responses":
        config["cache_keys"] = [f"chat-{index}" for index in range(40)]
    result = drive(config)
    after = command_counts()
    delta = {name: after.get(name, 0) - before.get(name, 0) for name in after}
    total = sum(
        count
        for name, count in delta.items()
        if name not in ("info", "client", "auth", "hello")
    )
    print(
        result.get("status"),
        "rps",
        result.get("rps"),
        "redis commands per request: %.1f" % (total / requests),
    )


def healthy(concurrency, requests):
    reset()
    result = drive({"n": requests, "conc": concurrency})
    print(
        result.get("status"), "p99_ms", result.get("p99_ms"), "rps", result.get("rps")
    )


def usage():
    reset()
    for _ in range(3):
        chat_once()
    time.sleep(3)
    print(
        "usage windows after 3 chat requests:", redis("hkeys", "agentek:usage").split()
    )


def stop(name):
    reset()
    mock({"defaults": dead_accounts(10)})
    thread = threading.Thread(target=lambda: drive({"duration": 60, "conc": 40}))
    thread.start()
    time.sleep(14)
    began = time.time()
    subprocess.run(["docker", "stop", "-t", "30", name], capture_output=True)
    code = subprocess.run(
        ["docker", "inspect", name, "-f", "{{.State.ExitCode}}"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    print(
        "docker stop -t 30 under load with dead accounts: took %.1fs, exit code %s"
        % (time.time() - began, code)
    )
    thread.join()


COMMANDS = {
    "setup": lambda a: setup(int(a[0])),
    "dead": lambda a: dead(int(a[0]), int(a[1]), int(a[2])),
    "cold": lambda a: cold(int(a[0]), int(a[1]), float(a[2])),
    "flush": lambda a: flush(),
    "calls": lambda a: calls(a[0], int(a[1])),
    "healthy": lambda a: healthy(int(a[0]), int(a[1])),
    "usage": lambda a: usage(),
    "stop": lambda a: stop(a[0]),
}

if __name__ == "__main__":
    COMMANDS[sys.argv[1]](sys.argv[2:])

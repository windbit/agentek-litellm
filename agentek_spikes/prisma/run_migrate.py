"""Runs the gateway's DB setup (same function the proxy calls at start) and prints the drift SQL before it is executed."""
import os, subprocess, sys

orig = subprocess.run


def run(args, *a, **k):
    if isinstance(args, list) and "execute" in args and "--file" in args:
        path = args[args.index("--file") + 1]
        print("DIFF_SQL_BEGIN\n" + open(path).read().strip() + "\nDIFF_SQL_END", flush=True)
    return orig(args, *a, **k)


subprocess.run = run
from litellm_proxy_extras.utils import ProxyExtrasDBManager

v2 = os.environ.get("V2") == "1"
print("RESULT", ProxyExtrasDBManager.setup_database(use_migrate=True, use_v2_resolver=v2), flush=True)

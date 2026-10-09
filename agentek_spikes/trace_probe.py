import urllib.request, time
for url in ("https://chatgpt.com/cdn-cgi/trace",):
    t0 = time.time()
    try:
        body = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=15).read().decode()
        d = dict(l.split("=", 1) for l in body.splitlines() if "=" in l)
        print({k: d.get(k) for k in ("ip", "loc", "colo", "http", "warp", "gateway")}, round(time.time() - t0, 2), "s")
    except Exception as err:
        print("ERR", type(err).__name__, err)

"""0.5: calling the startup hook twice in one process (hot reload / duplicated hook entry) must not raise or double-register."""
import asyncio, os
os.environ.setdefault("SPIKE_DIR", "/tmp/spike_idem")
os.makedirs("/tmp/spike_idem/logs", exist_ok=True)
import litellm
import agentek_gateway
from litellm.proxy.proxy_server import app


async def main():
    before_routes = len(app.routes)
    agentek_gateway.startup()
    first_routes = len(app.routes)
    try:
        agentek_gateway.startup()
        err = None
    except Exception as exc:
        err = repr(exc)
    print({"second_call_error": err, "routes_added_first": first_routes - before_routes,
           "routes_added_second": len(app.routes) - first_routes,
           "spike_loggers": sum(1 for cb in litellm.callbacks if type(cb).__name__ == "SpikeLogger")})

asyncio.run(main())

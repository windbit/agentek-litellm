"""Spike skeleton of the agentek_gateway plugin (part 0, not for merge).

Loaded through LITELLM_WORKER_STARTUP_HOOKS=agentek_gateway:startup.
"""
from .spike import startup

__all__ = ["startup"]

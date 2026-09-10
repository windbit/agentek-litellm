"""
Tests for the Codex client identity of the ChatGPT provider

Source: litellm/llms/chatgpt/codex_identity.py
"""

from typing import Callable, List

import httpx
import pytest

from litellm.llms.chatgpt.codex_identity import (
    CODEX_VERSION_FALLBACK,
    CODEX_VERSION_REFRESH_SECONDS,
    CodexVersion,
    codex_identity_headers,
    parse_codex_registry_version,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class DeferredRunner:
    def __init__(self) -> None:
        self.tasks: List[Callable[[], None]] = []

    def __call__(self, task: Callable[[], None]) -> None:
        self.tasks.append(task)

    def run_all(self) -> None:
        while self.tasks:
            self.tasks.pop(0)()


def _run_now(task: Callable[[], None]) -> None:
    task()


def test_request_gets_fallback_until_background_refresh_lands():
    runner = DeferredRunner()
    version = CodexVersion(
        fetch_latest=lambda: "99.0.0", run_in_background=runner, clock=FakeClock()
    )

    assert version.get() == CODEX_VERSION_FALLBACK
    runner.run_all()
    assert version.get() == "99.0.0"


def test_refreshes_at_most_once_per_interval():
    fetches: List[str] = []

    def fetch() -> str:
        fetches.append("fetch")
        return "99.0.0"

    clock = FakeClock()
    version = CodexVersion(fetch_latest=fetch, run_in_background=_run_now, clock=clock)

    version.get()
    version.get()
    assert len(fetches) == 1

    clock.now += CODEX_VERSION_REFRESH_SECONDS
    version.get()
    assert len(fetches) == 2


@pytest.mark.parametrize(
    "error",
    [httpx.ConnectError("registry unreachable"), ValueError("unexpected version")],
)
def test_failed_refresh_keeps_current_version(error):
    def fetch() -> str:
        raise error

    version = CodexVersion(
        fetch_latest=fetch, run_in_background=_run_now, clock=FakeClock()
    )

    assert version.get() == CODEX_VERSION_FALLBACK


def test_older_registry_version_does_not_downgrade():
    version = CodexVersion(
        fetch_latest=lambda: "0.100.0", run_in_background=_run_now, clock=FakeClock()
    )

    assert version.get() == CODEX_VERSION_FALLBACK


@pytest.mark.parametrize(
    "payload", [{}, {"version": "latest"}, {"version": "0.155.0-alpha.1"}]
)
def test_parse_registry_version_rejects_non_release(payload):
    with pytest.raises(ValueError):
        parse_codex_registry_version(payload)


def test_parse_registry_version_accepts_release():
    assert parse_codex_registry_version({"version": "0.154.0"}) == "0.154.0"


def test_identity_headers_declare_one_version():
    headers = codex_identity_headers()

    assert headers["version"] == CODEX_VERSION_FALLBACK
    assert headers["user-agent"].startswith(
        f"{headers['originator']}/{headers['version']} ("
    )

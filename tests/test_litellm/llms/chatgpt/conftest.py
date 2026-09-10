import pytest

from litellm.llms.chatgpt import codex_identity


@pytest.fixture(autouse=True)
def offline_codex_version(monkeypatch):
    """Pin the Codex version to its fallback instead of querying the npm registry."""
    monkeypatch.setattr(
        codex_identity,
        "_codex_version",
        codex_identity.CodexVersion(
            fetch_latest=lambda: codex_identity.CODEX_VERSION_FALLBACK,
            run_in_background=lambda task: None,
        ),
    )

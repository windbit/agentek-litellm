import httpx

from litellm.llms.custom_httpx.curl_cffi_transport import _browser_headers


def test_browser_headers_strips_fingerprint_headers_and_keeps_the_rest():
    request = httpx.Request(
        "POST",
        "https://chatgpt.com/backend-api/codex/chat/completions",
        headers={
            "user-agent": "python-httpx/0.28.1",
            "accept-encoding": "gzip, deflate",
            "connection": "keep-alive",
            "authorization": "Bearer sk-secret",
            "content-type": "application/json",
            "openai-beta": "responses-2025-01-01",
        },
    )

    result = {name.lower(): value for name, value in _browser_headers(request)}

    assert "user-agent" not in result
    assert "accept-encoding" not in result
    assert "connection" not in result
    assert result["authorization"] == "Bearer sk-secret"
    assert result["content-type"] == "application/json"
    assert result["openai-beta"] == "responses-2025-01-01"

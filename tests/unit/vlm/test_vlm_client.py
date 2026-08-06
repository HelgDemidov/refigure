"""Unit tests for refigure.vlm.client: retry ladder, error classification
(HTTP 4xx/429/5xx, in-band HTTP-200 error bodies, URLError/TimeoutError),
retry exhaustion, and OpenRouterClient/OpenAIClient/AnthropicClient's
send() payload shapes. OpenRouterClient's network calls are faked at the
urllib.request.urlopen level (no real network/API key); OpenAIClient/
AnthropicClient are faked one level up, by monkeypatching the constructed
SDK client's own .chat.completions.create()/.messages.create() method —
both SDKs are lazy at construction (no network call happens until a
request method is actually invoked), so building a real openai.OpenAI()/
anthropic.Anthropic() with a fake key here is safe. time.sleep is
monkeypatched to a no-op throughout, since RETRY_SCHEDULE sums to 80s of
real delay if left unmocked.
"""

from __future__ import annotations

import io
import json
import sys
import types
import urllib.error
import urllib.request
from email.message import Message
from typing import Any

import pytest

from refigure.api import MissingOptionalDependencyError
from refigure.vlm import client as vlm_client
from refigure.vlm.client import (
    RETRY_SCHEDULE,
    AnthropicClient,
    InbandError,
    OpenAIClient,
    OpenRouterClient,
    _parse_data_uri,
    chat_request,
)


class _FakeResponse:
    """Stand-in for the context-manager object urllib.request.urlopen()
    returns."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _chat_payload(text: str = "hello") -> dict[str, Any]:
    return {"choices": [{"message": {"content": text}}], "usage": {"cost": 0.01}}


def _http_error(code: int, body: bytes = b"{}") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://openrouter.ai/api/v1/chat/completions", code, "err", Message(), io.BytesIO(body)
    )


def _call(api_key: str = "test-key") -> dict[str, Any]:
    return chat_request({"model": "m", "messages": []}, api_key=api_key)


def _mock_no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    sleeps: list[float] = []
    monkeypatch.setattr(vlm_client.time, "sleep", lambda s: sleeps.append(s))
    return sleeps


# --- chat_request: success path ---


def test_chat_request_success_returns_response_unmodified(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _chat_payload("a rendered figure description")
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda req, timeout=1800.0: _FakeResponse(payload)
    )

    result = _call()

    assert result == payload


# --- chat_request: retry ladder ---


def test_chat_request_retries_on_http_500_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def fake_urlopen(req: Any, timeout: float = 1800.0) -> Any:
        calls["n"] += 1
        if calls["n"] <= 2:
            raise _http_error(500)
        return _FakeResponse(_chat_payload())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    sleeps = _mock_no_sleep(monkeypatch)

    result = _call()

    assert result["choices"][0]["message"]["content"] == "hello"
    assert calls["n"] == 3
    assert sleeps == [RETRY_SCHEDULE[0], RETRY_SCHEDULE[1]]


def test_chat_request_retries_on_http_429_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def fake_urlopen(req: Any, timeout: float = 1800.0) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error(429)
        return _FakeResponse(_chat_payload())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    sleeps = _mock_no_sleep(monkeypatch)

    result = _call()

    assert result["choices"][0]["message"]["content"] == "hello"
    assert calls["n"] == 2
    assert sleeps == [RETRY_SCHEDULE[0]]


def test_chat_request_retries_on_url_error_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def fake_urlopen(req: Any, timeout: float = 1800.0) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.URLError("network unreachable")
        return _FakeResponse(_chat_payload())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    sleeps = _mock_no_sleep(monkeypatch)

    result = _call()

    assert result["choices"][0]["message"]["content"] == "hello"
    assert sleeps == [RETRY_SCHEDULE[0]]


def test_chat_request_retries_on_timeout_error_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def fake_urlopen(req: Any, timeout: float = 1800.0) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("read timed out")
        return _FakeResponse(_chat_payload())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    sleeps = _mock_no_sleep(monkeypatch)

    result = _call()

    assert result["choices"][0]["message"]["content"] == "hello"
    assert sleeps == [RETRY_SCHEDULE[0]]


# --- chat_request: non-retryable HTTP errors raise immediately ---


def test_chat_request_http_400_raises_immediately_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def fake_urlopen(req: Any, timeout: float = 1800.0) -> Any:
        calls["n"] += 1
        raise _http_error(400, b"bad request")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    sleeps = _mock_no_sleep(monkeypatch)

    with pytest.raises(RuntimeError, match="400"):
        _call()

    assert calls["n"] == 1
    assert sleeps == []


def test_chat_request_http_413_raises_immediately_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """413 PayloadTooLarge is a non-retryable 4xx — retrying it would just
    fail identically five times instead of surfacing the real problem."""
    calls = {"n": 0}

    def fake_urlopen(req: Any, timeout: float = 1800.0) -> Any:
        calls["n"] += 1
        raise _http_error(413, b"payload too large")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    sleeps = _mock_no_sleep(monkeypatch)

    with pytest.raises(RuntimeError, match="413"):
        _call()

    assert calls["n"] == 1
    assert sleeps == []


# --- InbandError classification ---


@pytest.mark.parametrize("code", [429, 500, 503])
def test_inband_error_retryable_codes(code: int) -> None:
    err = InbandError(code, "body")
    assert err.retryable is True


@pytest.mark.parametrize("code", [400, 404, None])
def test_inband_error_non_retryable_codes(code: Any) -> None:
    err = InbandError(code, "body")
    assert err.retryable is False


def test_chat_request_inband_error_400_raises_immediately_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP 200 with an {"error": ...} body and a non-retryable code."""
    calls = {"n": 0}

    def fake_urlopen(req: Any, timeout: float = 1800.0) -> Any:
        calls["n"] += 1
        return _FakeResponse({"error": {"message": "bad request", "code": 400}})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    sleeps = _mock_no_sleep(monkeypatch)

    with pytest.raises(RuntimeError, match="error in a 200 body"):
        _call()

    assert calls["n"] == 1
    assert sleeps == []


def test_chat_request_inband_error_429_is_retried_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP 200 with an {"error": ...} body and a retryable code (429)."""
    calls = {"n": 0}

    def fake_urlopen(req: Any, timeout: float = 1800.0) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResponse({"error": {"message": "rate limited", "code": 429}})
        return _FakeResponse(_chat_payload())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    sleeps = _mock_no_sleep(monkeypatch)

    result = _call()

    assert result["choices"][0]["message"]["content"] == "hello"
    assert calls["n"] == 2
    assert sleeps == [RETRY_SCHEDULE[0]]


# --- chat_request: retry exhaustion ---


def test_chat_request_exhausts_retries_on_persistent_500(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def fake_urlopen(req: Any, timeout: float = 1800.0) -> Any:
        calls["n"] += 1
        raise _http_error(500)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    sleeps = _mock_no_sleep(monkeypatch)

    with pytest.raises(RuntimeError, match="attempts exhausted"):
        _call()

    assert calls["n"] == len(RETRY_SCHEDULE) + 1
    assert sleeps == list(RETRY_SCHEDULE)


def test_chat_request_exhausts_retries_on_persistent_url_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def fake_urlopen(req: Any, timeout: float = 1800.0) -> Any:
        calls["n"] += 1
        raise urllib.error.URLError("network unreachable")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    sleeps = _mock_no_sleep(monkeypatch)

    with pytest.raises(RuntimeError, match="attempts exhausted"):
        _call()

    assert calls["n"] == len(RETRY_SCHEDULE) + 1
    assert sleeps == list(RETRY_SCHEDULE)


# --- API key never leaks into an exception message ---


def test_api_key_never_appears_in_non_retryable_exception_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda req, timeout=1800.0: (_ for _ in ()).throw(_http_error(400, b"bad request")),
    )
    secret = "super-secret-key-12345"

    with pytest.raises(RuntimeError) as exc_info:
        chat_request({"model": "m", "messages": []}, api_key=secret)

    assert secret not in str(exc_info.value)


def test_api_key_never_appears_in_exhausted_retries_exception_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda req, timeout=1800.0: (_ for _ in ()).throw(_http_error(500)),
    )
    _mock_no_sleep(monkeypatch)
    secret = "super-secret-key-67890"

    with pytest.raises(RuntimeError) as exc_info:
        chat_request({"model": "m", "messages": []}, api_key=secret)

    assert secret not in str(exc_info.value)


# --- OpenRouterClient.send() ---


def test_openrouter_client_send_returns_content(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda req, timeout=1800.0: _FakeResponse(_chat_payload("a figure showing revenue growth")),
    )

    client = OpenRouterClient(api_key="test-key")
    content = client.send("Describe this chart.", "data:image/png;base64,AAAA", model="some/model")

    assert content == "a figure showing revenue growth"


def test_openrouter_client_send_builds_expected_payload_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_urlopen(req: Any, timeout: float = 1800.0) -> Any:
        captured["request"] = req
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return _FakeResponse(_chat_payload())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    client = OpenRouterClient(api_key="test-key")
    prompt = "Describe the chart in this image."
    image_uri = "data:image/png;base64,BBBB"
    client.send(prompt, image_uri, model="vendor/model-x")

    payload = captured["payload"]
    assert payload["model"] == "vendor/model-x"
    assert len(payload["messages"]) == 1
    message = payload["messages"][0]
    assert message["role"] == "user"

    content_blocks = message["content"]
    text_blocks = [b for b in content_blocks if b["type"] == "text"]
    image_blocks = [b for b in content_blocks if b["type"] == "image_url"]
    assert len(text_blocks) == 1
    assert text_blocks[0]["text"] == prompt
    assert len(image_blocks) == 1
    assert image_blocks[0]["image_url"]["url"] == image_uri


def test_openrouter_client_send_uses_constructor_api_key_for_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_urlopen(req: Any, timeout: float = 1800.0) -> Any:
        captured["request"] = req
        return _FakeResponse(_chat_payload())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    client = OpenRouterClient(api_key="the-configured-key")
    client.send("prompt", "data:image/png;base64,CCCC", model="some/model")

    request = captured["request"]
    assert request.get_header("Authorization") == "Bearer the-configured-key"


def test_openrouter_client_send_propagates_non_retryable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda req, timeout=1800.0: (_ for _ in ()).throw(_http_error(400, b"bad request")),
    )

    client = OpenRouterClient(api_key="test-key")
    with pytest.raises(RuntimeError, match="400"):
        client.send("prompt", "data:image/png;base64,DDDD", model="some/model")


# --- OpenAIClient.send() ---


def _fake_openai_response(text: str = "hello") -> Any:
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=text))]
    )


def test_openai_client_send_returns_content(monkeypatch: pytest.MonkeyPatch) -> None:
    client = OpenAIClient(api_key="test-key")
    monkeypatch.setattr(
        client._client.chat.completions,
        "create",
        lambda **kwargs: _fake_openai_response("a figure showing revenue growth"),
    )

    content = client.send("Describe this chart.", "data:image/png;base64,AAAA", model="some-model")

    assert content == "a figure showing revenue growth"


def test_openai_client_send_builds_dict_image_format_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_create(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _fake_openai_response()

    client = OpenAIClient(api_key="test-key")
    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)

    prompt = "Describe the chart in this image."
    image_uri = "data:image/png;base64,BBBB"
    client.send(prompt, image_uri, model="vendor/model-x")

    assert captured["model"] == "vendor/model-x"
    message = captured["messages"][0]
    assert message["role"] == "user"
    content_blocks = message["content"]
    text_blocks = [b for b in content_blocks if b["type"] == "text"]
    image_blocks = [b for b in content_blocks if b["type"] == "image_url"]
    assert text_blocks == [{"type": "text", "text": prompt}]
    assert image_blocks[0]["image_url"] == {"url": image_uri}


def test_openai_client_send_uses_bare_string_image_format_for_ollama(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``image_content_format="string"`` is required for Ollama's OpenAI-
    compat vision endpoint (confirmed live against docs.ollama.com, see the
    class docstring) — a bare data: URI string, not OpenRouterClient's
    nested {"url": ...} dict."""
    captured: dict[str, Any] = {}

    def fake_create(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _fake_openai_response()

    client = OpenAIClient(
        api_key="unused",
        base_url="http://localhost:11434/v1/",
        image_content_format="string",
    )
    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)

    image_uri = "data:image/png;base64,CCCC"
    client.send("prompt", image_uri, model="llava")

    image_block = next(b for b in captured["messages"][0]["content"] if b["type"] == "image_url")
    assert image_block["image_url"] == image_uri


def test_openai_client_missing_dependency_raises_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "openai", None)

    with pytest.raises(MissingOptionalDependencyError, match="vlm-direct"):
        OpenAIClient(api_key="x")


# --- AnthropicClient.send() ---


def _fake_anthropic_response(text: str = "hello") -> Any:
    return types.SimpleNamespace(content=[types.SimpleNamespace(text=text)])


def test_anthropic_client_send_returns_content(monkeypatch: pytest.MonkeyPatch) -> None:
    client = AnthropicClient(api_key="test-key")
    monkeypatch.setattr(
        client._client.messages,
        "create",
        lambda **kwargs: _fake_anthropic_response("a figure showing revenue growth"),
    )

    content = client.send(
        "Describe this chart.",
        "data:image/png;base64,AAAA",
        model="claude-haiku-4-5-20251001",
    )

    assert content == "a figure showing revenue growth"


def test_anthropic_client_send_builds_expected_content_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_create(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _fake_anthropic_response()

    client = AnthropicClient(api_key="test-key")
    monkeypatch.setattr(client._client.messages, "create", fake_create)

    prompt = "Describe the chart in this image."
    client.send(prompt, "data:image/png;base64,BBBB", model="claude-opus-5")

    assert captured["model"] == "claude-opus-5"
    message = captured["messages"][0]
    assert message["role"] == "user"
    text_blocks = [b for b in message["content"] if b["type"] == "text"]
    image_blocks = [b for b in message["content"] if b["type"] == "image"]
    assert text_blocks == [{"type": "text", "text": prompt}]
    assert image_blocks == [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "BBBB"},
        }
    ]


def test_anthropic_client_missing_dependency_raises_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "anthropic", None)

    with pytest.raises(MissingOptionalDependencyError, match="vlm-direct"):
        AnthropicClient(api_key="x")


def test_anthropic_client_uses_injected_client_as_is(monkeypatch: pytest.MonkeyPatch) -> None:
    """``client=`` must be used verbatim, not reconstructed — proves the
    Bedrock/Vertex/Foundry injection design (see the class docstring)
    doesn't need any AnthropicClient-side special-casing per platform."""
    calls: list[dict[str, Any]] = []

    class _FakeMessages:
        def create(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            return _fake_anthropic_response("via injected client")

    fake_client = types.SimpleNamespace(messages=_FakeMessages())

    # anthropic doesn't even need to be importable for this path — api_key
    # is unused and the real anthropic.Anthropic() constructor never runs.
    monkeypatch.setitem(sys.modules, "anthropic", None)

    client = AnthropicClient(client=fake_client)
    content = client.send(
        "prompt", "data:image/jpeg;base64,DDDD", model="global.anthropic.claude-opus-4-6-v1"
    )

    assert content == "via injected client"
    assert client._client is fake_client
    assert len(calls) == 1


# --- _parse_data_uri ---


def test_parse_data_uri_splits_media_type_and_data() -> None:
    media_type, data = _parse_data_uri("data:image/png;base64,AAAABBBB")
    assert media_type == "image/png"
    assert data == "AAAABBBB"


@pytest.mark.parametrize(
    "bad_uri",
    ["not-a-data-uri", "data:image/png,missing-base64-marker", ""],
)
def test_parse_data_uri_rejects_non_data_uri(bad_uri: str) -> None:
    with pytest.raises(ValueError, match="data: URI"):
        _parse_data_uri(bad_uri)

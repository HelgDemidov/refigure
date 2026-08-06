"""``VlmClient`` implementations (``api.py``'s Protocol, see its docstring):
``OpenRouterClient`` (default, calling OpenRouter's ``chat/completions``
endpoint), ``OpenAIClient`` (direct OpenAI or any OpenAI-compatible
endpoint — Ollama/vLLM/LM Studio, via ``base_url``), and ``AnthropicClient``
(direct Anthropic Messages API). refigure is LLM provider-agnostic by
design; a caller can also supply any other ``VlmClient`` entirely to
``Config.vlm_client``.

``chat_request``/``InbandError``/``RETRY_SCHEDULE`` are a near-verbatim port
of the source pipeline's ``core/openrouter.py`` (confirmed fully
synchronous by reading it, not assumed — see ``docs/project-meta/
v1-scope-and-api-design/v1-scope-and-api-design-2026-08-04.md`` §3): the
retry/error-classification logic itself needs no redesign, only the
payload-building layer around it (previously ``figures_vlm.py``'s
``_build_payload``/``_call_vlm_uri``) is new, folded into
``OpenRouterClient.send``.

``OpenAIClient``/``AnthropicClient`` are the one deliberate exception to
this package's usual module-level ``try/except`` optional-dependency guard
(see ``refigure/xlsx/__init__.py`` for the usual shape): ``OpenRouterClient``
above has zero third-party dependencies (stdlib ``urllib`` only), and this
property must survive adding the two SDK-backed clients — importing this
module must NOT require ``openai``/``anthropic`` to be installed, only
constructing ``OpenAIClient()``/``AnthropicClient()`` should. So each class
does its own ``try/except ImportError`` INSIDE ``__init__``, not at module
level — not a departure from the optional-dependency pattern's intent
(typed ``MissingOptionalDependencyError``, not a bare ``ImportError``),
just applied per-class instead of per-module, because this one module now
hosts multiple independent capabilities with different dependencies rather
than one capability per module. See
``docs/vlm/vlm-direct-clients/vlm-direct-clients-2026-08-06.md`` §4.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Literal

from ..api import MissingOptionalDependencyError

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
RETRY_SCHEDULE = (1.0, 4.0, 15.0, 60.0)

# Figure-interpretation request tuning (source pipeline's figures_vlm.py
# FIG_MAX_TOKENS/FIG_REQUEST): baked in here, not part of the VlmClient
# Protocol — the Protocol's send() signature is deliberately minimal
# (prompt, image_uri, model) so any implementation, including a future
# local one, can satisfy it without needing these OpenRouter-specific knobs.
_DEFAULT_MAX_TOKENS = 8000
_DEFAULT_REQUEST_EXTRA: dict[str, Any] = {"reasoning": {"effort": "low"}}


class InbandError(Exception):
    """An error that arrived in the BODY of an HTTP-200 OpenRouter response
    (``{"error": {...}}``) — there is no transport-level HTTPError;
    retryability is decided from the body's code with the same logic as for
    HTTP status codes: 429/5xx are transient, everything else is not."""

    def __init__(self, code: Any, body: str) -> None:
        super().__init__(body)
        self.body = body
        self.retryable = code == 429 or (isinstance(code, int) and code >= 500)


def _request(payload: dict[str, Any], *, api_key: str, timeout: float) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        OPENROUTER_CHAT_URL,
        data=data,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — fixed https URL, not user input
        body: Any = json.loads(resp.read())
    if "choices" not in body:
        # OpenRouter wraps provider-side errors in an HTTP 200 with
        # {"error": ...} — there's no transport HTTPError, so the failure
        # class is mapped from the code INSIDE the body instead.
        err = body.get("error") or {}
        raise InbandError(err.get("code"), json.dumps(body, ensure_ascii=False)[:500])
    return body  # type: ignore[no-any-return]


def chat_request(
    payload: dict[str, Any], *, api_key: str, timeout: float = 1800.0
) -> dict[str, Any]:
    """POST + retry ladder: 429/5xx/``URLError``/``TimeoutError``/retryable
    ``InbandError`` -> up to 5 attempts total; any other 4xx (including 413
    PayloadTooLarge) -> immediate ``RuntimeError`` with the response body.
    Returns the full JSON response (the caller extracts ``choices``/
    ``usage`` itself). The API key never ends up in a log line or an
    exception message. ``timeout=1800`` by default: a non-streaming
    multi-thousand-token generation on a slow provider can exceed 900s.
    """
    reason = ""
    total_attempts = len(RETRY_SCHEDULE) + 1
    for attempt in range(1, total_attempts + 1):
        try:
            return _request(payload, api_key=api_key, timeout=timeout)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            if exc.code != 429 and exc.code < 500:
                raise RuntimeError(f"OpenRouter HTTP {exc.code}: {body}") from exc
            reason = f"HTTP {exc.code}: {body}"
        except InbandError as exc:
            if not exc.retryable:
                raise RuntimeError(f"OpenRouter (error in a 200 body): {exc.body}") from exc
            reason = f"error in a 200 body: {exc.body}"
        except (urllib.error.URLError, TimeoutError) as exc:
            reason = str(exc)
        if attempt == total_attempts:
            break
        delay = RETRY_SCHEDULE[attempt - 1]
        print(
            f"attempt {attempt}/{total_attempts}, retrying in {delay:.0f}s: {reason}",
            file=sys.stderr,
        )
        time.sleep(delay)
    raise RuntimeError(f"OpenRouter: attempts exhausted ({total_attempts}) — {reason}")


class OpenRouterClient:
    """Default ``VlmClient`` (``api.py``) implementation. ``api_key`` is an
    instance field, not a ``send()`` parameter — an implementation detail
    the Protocol doesn't dictate (a different implementation might need no
    key at all, e.g. a local model)."""

    def __init__(
        self, api_key: str, *, max_tokens: int = _DEFAULT_MAX_TOKENS, timeout: float = 1800.0
    ) -> None:
        self.api_key = api_key
        self.max_tokens = max_tokens
        self.timeout = timeout

    def send(self, prompt: str, image_uri: str, *, model: str) -> str:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_uri}},
                    ],
                }
            ],
            "max_tokens": self.max_tokens,
            **_DEFAULT_REQUEST_EXTRA,
        }
        response = chat_request(payload, api_key=self.api_key, timeout=self.timeout)
        content = response["choices"][0]["message"]["content"]
        return content  # type: ignore[no-any-return]


class OpenAIClient:
    """``VlmClient`` for direct OpenAI or any OpenAI-compatible endpoint,
    via the official ``openai`` package (not a hand-rolled ``urllib`` call
    like ``OpenRouterClient`` above — the SDK gives typed requests + its
    own retry for a modest, well-audited dependency, see
    ``docs/vlm/vlm-direct-clients/vlm-direct-clients-2026-08-06.md`` §1).

    ``base_url`` covers more than "direct OpenAI": Ollama/vLLM/LM Studio
    all speak the same ``/v1/chat/completions`` dialect (confirmed live
    against ``docs.ollama.com/api/openai-compatibility``,
    ``OpenAI(base_url="http://localhost:11434/v1/", api_key="ollama")``),
    so pointing ``base_url`` at a local server is the whole story for
    local/confidentiality-sensitive inference — no separate local client.

    ``image_content_format="string"`` is REQUIRED when ``base_url`` targets
    Ollama specifically: its vision endpoint wants
    ``"image_url": "data:image/png;base64,..."`` (a bare string), not
    ``{"url": "..."}`` (the nested dict real OpenAI and OpenRouter expect)
    — confirmed live, not assumed. Not auto-detected from ``base_url`` (a
    URL-pattern heuristic would be fragile) — an explicit constructor
    parameter, same principle as ``Config.vlm_judge_mode``.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        image_content_format: Literal["dict", "string"] = "dict",
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        timeout: float = 1800.0,
    ) -> None:
        try:
            import openai
        except ImportError as exc:
            raise MissingOptionalDependencyError(
                "refigure[vlm-direct] is required to use OpenAIClient"
            ) from exc
        self._client = openai.OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self.image_content_format = image_content_format
        self.max_tokens = max_tokens

    def send(self, prompt: str, image_uri: str, *, model: str) -> str:
        image_url: Any = image_uri if self.image_content_format == "string" else {"url": image_uri}
        response = self._client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": image_url},
                    ],
                }
            ],
            max_tokens=self.max_tokens,
        )
        content = response.choices[0].message.content
        return content or ""

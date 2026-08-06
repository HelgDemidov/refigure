"""Live opt-in integration test for ``AnthropicClient`` via Microsoft
Foundry (Azure) — see
docs/vlm/vlm-direct-clients/vlm-direct-clients-2026-08-06.md §3.

Proves plumbing, not accuracy — see test_anthropic_bedrock_live.py's
module docstring for the full rationale (same discipline, same NOT-in-CI
boundary, same explicit-opt-in-over-ambient-credentials reasoning).

Requires:
    REFIGURE_LIVE_FOUNDRY=1         — explicit opt-in
    ANTHROPIC_FOUNDRY_API_KEY       — from the Foundry portal's deployment
                                       Details tab
    ANTHROPIC_FOUNDRY_RESOURCE      — your Foundry resource name (or set
                                       ANTHROPIC_FOUNDRY_BASE_URL instead)
    + a provisioned Foundry resource + Claude deployment (ai.azure.com — a
      real one-time setup step, not automatic)
    + no extra package beyond base ``anthropic`` (unlike Bedrock/Vertex,
      ``AnthropicFoundry`` ships in the base package, per
      platform.claude.com, 2026-08-06)

``AnthropicFoundry()`` reads ``ANTHROPIC_FOUNDRY_API_KEY``/
``ANTHROPIC_FOUNDRY_RESOURCE``/``ANTHROPIC_FOUNDRY_BASE_URL`` from the
environment itself when constructed with no arguments — this test doesn't
pass them explicitly, matching the SDK's own documented convention.

Optional override (default targets the cheapest current model):
    REFIGURE_LIVE_FOUNDRY_MODEL   default "claude-haiku-4-5" (Foundry's
                                   default deployment name for that model —
                                   override if you created a custom
                                   deployment name)
"""

from __future__ import annotations

import os

import pytest

from refigure.vlm.client import AnthropicClient

_ENABLED = os.environ.get("REFIGURE_LIVE_FOUNDRY", "").strip() not in ("", "0", "false")

pytestmark = pytest.mark.skipif(
    not _ENABLED,
    reason="REFIGURE_LIVE_FOUNDRY not set — opt-in only, real Azure spend/creds required",
)

# 1x1 red pixel PNG, the smallest valid image — see
# test_anthropic_bedrock_live.py for why this is enough for a plumbing test.
_TINY_PNG_DATA_URI = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lE"
    "QVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def test_anthropic_client_via_foundry_returns_real_response() -> None:
    try:
        from anthropic import AnthropicFoundry
    except ImportError:
        pytest.skip("anthropic package too old for AnthropicFoundry")

    has_resource = os.environ.get("ANTHROPIC_FOUNDRY_RESOURCE") or os.environ.get(
        "ANTHROPIC_FOUNDRY_BASE_URL"
    )
    if not (os.environ.get("ANTHROPIC_FOUNDRY_API_KEY") and has_resource):
        pytest.skip("ANTHROPIC_FOUNDRY_API_KEY and ANTHROPIC_FOUNDRY_RESOURCE/_BASE_URL not set")

    model = os.environ.get("REFIGURE_LIVE_FOUNDRY_MODEL", "claude-haiku-4-5")
    client = AnthropicClient(client=AnthropicFoundry())

    content = client.send(
        "What color is this image? Answer in one word.",
        _TINY_PNG_DATA_URI,
        model=model,
    )

    assert isinstance(content, str)
    assert content.strip() != ""

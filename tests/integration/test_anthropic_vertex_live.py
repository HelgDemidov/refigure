"""Live opt-in integration test for ``AnthropicClient`` via Google Cloud's
Agent Platform (Vertex) — see
docs/vlm/vlm-direct-clients/vlm-direct-clients-2026-08-06.md §3.

Proves plumbing, not accuracy — see test_anthropic_bedrock_live.py's
module docstring for the full rationale (same discipline, same NOT-in-CI
boundary, same explicit-opt-in-over-ambient-credentials reasoning).

Requires:
    REFIGURE_LIVE_VERTEX=1               — explicit opt-in
    REFIGURE_LIVE_VERTEX_PROJECT=<id>     — your GCP project ID, no default
    + a GCP project with Claude enabled in Model Garden (a real one-time
      setup step, not automatic), and Application Default Credentials
      resolvable (``gcloud auth application-default login``, or a service
      account)
    + ``anthropic[vertex]`` installed (``google-auth`` — NOT part of the
      base ``vlm-direct`` extra, install separately)

Optional overrides (defaults target the cheapest current model):
    REFIGURE_LIVE_VERTEX_REGION   default "global"
    REFIGURE_LIVE_VERTEX_MODEL    default "claude-haiku-4-5@20251001"
"""

from __future__ import annotations

import os

import pytest

from refigure.vlm.client import AnthropicClient

_ENABLED = os.environ.get("REFIGURE_LIVE_VERTEX", "").strip() not in ("", "0", "false")

pytestmark = pytest.mark.skipif(
    not _ENABLED,
    reason="REFIGURE_LIVE_VERTEX not set — opt-in only, real GCP spend/creds required",
)

# 1x1 red pixel PNG, the smallest valid image — see
# test_anthropic_bedrock_live.py for why this is enough for a plumbing test.
_TINY_PNG_DATA_URI = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lE"
    "QVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def test_anthropic_client_via_vertex_returns_real_response() -> None:
    # The real gate is google-auth, not AnthropicVertex's own importability
    # — same class of gap as test_anthropic_bedrock_live.py's boto3 case,
    # found live while writing this test: AnthropicVertex imports cleanly
    # without google-auth installed, but raises its own
    # MissingDependencyError only once actually constructed.
    try:
        import google.auth  # noqa: F401
        from anthropic import AnthropicVertex
    except ImportError:
        pytest.skip("anthropic[vertex] not installed (google-auth missing)")

    project_id = os.environ.get("REFIGURE_LIVE_VERTEX_PROJECT")
    if not project_id:
        pytest.skip("REFIGURE_LIVE_VERTEX_PROJECT not set")

    region = os.environ.get("REFIGURE_LIVE_VERTEX_REGION", "global")
    model = os.environ.get("REFIGURE_LIVE_VERTEX_MODEL", "claude-haiku-4-5@20251001")
    client = AnthropicClient(client=AnthropicVertex(project_id=project_id, region=region))

    content = client.send(
        "What color is this image? Answer in one word.",
        _TINY_PNG_DATA_URI,
        model=model,
    )

    assert isinstance(content, str)
    assert content.strip() != ""

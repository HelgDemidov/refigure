"""Live opt-in integration test for ``AnthropicClient`` via Amazon Bedrock.

Proves plumbing (the ``client=`` injection actually round-trips a real
Bedrock call), not accuracy — payload-shape coverage against fakes already
lives in tests/unit/vlm/test_vlm_client.py. NOT wired into
.github/workflows/ci.yml: no CI job here holds real AWS credentials, same
discipline as this project's prior OpenRouterClient smoke tests (run
manually by a developer with a real account before merging, not
automated). Ambient AWS credentials on a dev machine are not consent to
spend real money on a real enterprise cloud — hence the explicit opt-in
flag below, not just "skip if boto3/creds happen to be missing".

Requires:
    REFIGURE_LIVE_BEDROCK=1   — explicit opt-in
    + real AWS credentials resolvable by the standard chain (env vars,
      ``~/.aws/credentials``, or ``AWS_BEARER_TOKEN_BEDROCK``) with Bedrock
      model access granted for the target model (AWS Console > Bedrock >
      Model Access — a real one-time setup step, not automatic)
    + ``anthropic[bedrock]`` installed (``boto3``/``botocore`` — NOT part
      of the base ``vlm-direct`` extra, install separately)

Optional overrides (defaults target the cheapest current model):
    REFIGURE_LIVE_BEDROCK_REGION   default "us-east-1"
    REFIGURE_LIVE_BEDROCK_MODEL    default "anthropic.claude-haiku-4-5-20251001-v1:0"
                                    — override if your account/region needs
                                    an inference-profile-prefixed ID instead
                                    (e.g. "us.anthropic...."), see Amazon
                                    Bedrock's own docs for your setup.
"""

from __future__ import annotations

import os

import pytest

from refigure.vlm.client import AnthropicClient

_ENABLED = os.environ.get("REFIGURE_LIVE_BEDROCK", "").strip() not in ("", "0", "false")

pytestmark = pytest.mark.skipif(
    not _ENABLED,
    reason="REFIGURE_LIVE_BEDROCK not set — opt-in only, real AWS spend/creds required",
)

# 1x1 red pixel PNG, the smallest valid image — this test proves the
# request round-trips for real, not that the model describes it well.
_TINY_PNG_DATA_URI = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lE"
    "QVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def test_anthropic_client_via_bedrock_returns_real_response() -> None:
    # The real gate is boto3, not AnthropicBedrock's own importability:
    # AnthropicBedrock imports cleanly from base anthropic even without
    # boto3/botocore installed, but request-signing (botocore.auth.
    # SigV4Auth) fails deep inside client.send() instead — found live while
    # writing this test (a bare `from anthropic import AnthropicBedrock`
    # guard let a missing-dependency case through as a hard FAIL, not skip).
    try:
        import boto3  # noqa: F401
        from anthropic import AnthropicBedrock
    except ImportError:
        pytest.skip("anthropic[bedrock] not installed (boto3/botocore missing)")

    region = os.environ.get("REFIGURE_LIVE_BEDROCK_REGION", "us-east-1")
    model = os.environ.get(
        "REFIGURE_LIVE_BEDROCK_MODEL", "anthropic.claude-haiku-4-5-20251001-v1:0"
    )
    client = AnthropicClient(client=AnthropicBedrock(aws_region=region))

    content = client.send(
        "What color is this image? Answer in one word.",
        _TINY_PNG_DATA_URI,
        model=model,
    )

    assert isinstance(content, str)
    assert content.strip() != ""

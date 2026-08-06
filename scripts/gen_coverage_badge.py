"""Generate a shields.io endpoint-badge JSON from `coverage json`'s summary.

Run in CI (ci.yml's "coverage" job) right after `coverage combine` +
`coverage report --fail-under=95` + `coverage json -o coverage.json` —
transforms the "totals.percent_covered" field into shields.io's endpoint
schema (https://shields.io/badges/endpoint-badge), written to
docs/assets/coverage-badge.json and committed on push to main only.

Self-hosted, not a Codecov/Coveralls account — no third-party service or
token, same principle already applied to OIDC PyPI publishing / rejecting
LiteLLM (docs/testing/coverage-hardening/coverage-hardening-2026-08-06.md
§4). Pure stdlib, no dependencies, runnable locally for testing:

    coverage json -o coverage.json
    python scripts/gen_coverage_badge.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_COVERAGE_JSON = REPO / "coverage.json"
_OUT_PATH = REPO / "docs" / "assets" / "coverage-badge.json"


def _color(pct: float) -> str:
    """shields.io convention. In practice the CI job only reaches this
    script after `coverage report --fail-under=95` already passed, so pct
    is always >= 95 (brightgreen) today — tiered anyway so a future
    threshold change doesn't need this function touched too."""
    if pct >= 95:
        return "brightgreen"
    if pct >= 90:
        return "green"
    if pct >= 80:
        return "yellow"
    return "red"


def main() -> int:
    data = json.loads(_COVERAGE_JSON.read_text(encoding="utf-8"))
    pct = float(data["totals"]["percent_covered"])
    badge = {
        "schemaVersion": 1,
        "label": "coverage",
        "message": f"{pct:.1f}%",
        "color": _color(pct),
    }
    _OUT_PATH.write_text(json.dumps(badge, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {_OUT_PATH} ({badge['message']}, {badge['color']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

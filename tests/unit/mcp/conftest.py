"""Shared anyio-backend fixture for tests/unit/mcp/.

Architecture doc §9's pattern: anyio's own pytest plugin
(``@pytest.mark.anyio``), never pytest-asyncio — reproduced live against
this project's target ``mcp``/``anyio`` versions during architecture
review (a conflict between anyio's cancel-scope semantics and
pytest-asyncio's fresh-task-per-fixture teardown was the concern; not
depending on pytest-asyncio at all sidesteps it entirely, not works around
it). No extra dependency: anyio's pytest plugin ships inside ``anyio``
itself, already required transitively via ``[mcp]``.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"

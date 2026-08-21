"""``refigure/mcp/cli.py`` — the ``refigure-mcp`` console entry point.

Never actually starts a real server (``build_server``/``MCPServer.run`` are
mocked throughout) — this file proves the CLI's OWN responsibility: flag
parsing/wiring and the exception-to-exit-code boundary, mirroring
``tests/unit/test_cli.py``'s own division of labor for the main
``refigure`` CLI (its own module docstring: 'these tests only prove the
CLI builds the right Config/vlm_client, not that the engine works').
"""

from __future__ import annotations

import pytest

import refigure.mcp.cli as mcp_cli_module
from refigure.cli import EXIT_INTERNAL_ERROR, EXIT_MISSING_DEPENDENCY


class _FakeServer:
    def __init__(self) -> None:
        self.ran_with: str | None = None

    def run(self, transport: str) -> None:
        self.ran_with = transport


def _capture_build_server(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    captured: dict[str, object] = {}
    server = _FakeServer()

    def _fake_build_server(**kwargs: object) -> _FakeServer:
        captured.update(kwargs)
        return server

    monkeypatch.setattr(mcp_cli_module, "build_server", _fake_build_server)
    captured["_server"] = server
    return captured


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        mcp_cli_module.main(["--help"])
    assert exc_info.value.code == 0
    assert "convert_docx" in capsys.readouterr().out


def test_version_exits_zero_and_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        mcp_cli_module.main(["--version"])
    assert exc_info.value.code == 0
    assert "refigure-mcp" in capsys.readouterr().out


def test_no_flags_uses_build_server_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Optional-kwargs-dict pattern, same as refigure.cli._build_config:
    an unset flag never enters kwargs, so build_server's own defaults
    apply — never duplicated/re-declared here (see server.py's own
    build_server docstring for why)."""
    captured = _capture_build_server(monkeypatch)

    code = mcp_cli_module.main([])

    assert code == 0
    assert "max_concurrent" not in captured
    assert "max_input_b64_mb" not in captured
    assert "timeout_s" not in captured
    assert "vlm_max_markers" not in captured
    assert captured["transport"] == "stdio"
    server = captured["_server"]
    assert isinstance(server, _FakeServer)
    assert server.ran_with == "stdio"


@pytest.mark.parametrize(
    "flag,value,kwarg,expected",
    [
        ("--mcp-max-concurrent-conversions", "8", "max_concurrent", 8),
        ("--mcp-max-input-mb", "50", "max_input_b64_mb", 50),
        ("--mcp-conversion-timeout-s", "120", "timeout_s", 120),
        ("--vlm-max-markers", "10", "vlm_max_markers", 10),
    ],
)
def test_numeric_flags_reach_build_server(
    monkeypatch: pytest.MonkeyPatch, flag: str, value: str, kwarg: str, expected: int
) -> None:
    captured = _capture_build_server(monkeypatch)

    code = mcp_cli_module.main([flag, value])

    assert code == 0
    assert captured[kwarg] == expected


def test_vlm_provider_openrouter_default_needs_no_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The openrouter default is the cheap, zero-construction path (see
    _resolve_vlm_client) — main() must not require any credentials just
    to start the server when no operator-set VLM flags are present."""
    captured = _capture_build_server(monkeypatch)

    code = mcp_cli_module.main([])

    assert code == 0
    assert captured.get("vlm_client") is None


def test_vlm_api_key_file_resolves_once_at_startup(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    captured = _capture_build_server(monkeypatch)
    key_file = tmp_path / "key.txt"
    key_file.write_text("sk-or-abc123\n")

    code = mcp_cli_module.main(["--vlm-api-key-file", str(key_file)])

    assert code == 0
    assert captured["vlm_api_key"] == "sk-or-abc123"
    assert captured.get("vlm_client") is None


def test_vlm_provider_openai_missing_credentials_is_a_clean_exit_not_a_crash(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Regression guard, same class as refigure.cli's own
    test_vlm_provider_openai_missing_credentials_is_typed_error_not_a_crash:
    _resolve_vlm_client constructs a REAL openai.OpenAI(...) client eagerly,
    before build_server is ever called — its own SDK exception (not one of
    refigure's typed exceptions) must route through _exit_code_for, never
    escape as a raw traceback (CLAUDE.md's 'Do NOT' entry on exactly this
    pattern)."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    code = mcp_cli_module.main(["--vlm-provider", "openai"])

    assert code == EXIT_INTERNAL_ERROR
    assert "error:" in capsys.readouterr().err


def test_vlm_provider_openai_without_vlm_direct_extra_is_missing_dependency() -> None:
    """Complement of the test above, via the same sys.modules-poisoning
    technique tests/unit/test_optional_dependency_guards.py already uses
    — needs a fresh subprocess (poisoning only prevents the FIRST import
    of a module in one process)."""
    import subprocess
    import sys

    from tests.support import REPO_ROOT

    script = (
        "import sys\n"
        "sys.modules['openai'] = None\n"
        "from refigure.mcp.cli import main\n"
        "sys.exit(main(['--vlm-provider', 'openai']))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=30,
    )
    assert result.returncode == EXIT_MISSING_DEPENDENCY
    assert "refigure[vlm-direct]" in result.stderr

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
from refigure.cli import EXIT_INTERNAL_ERROR, EXIT_MISSING_DEPENDENCY, EXIT_USAGE


class _FakeServer:
    def __init__(self) -> None:
        self.ran_with: str | None = None
        self.run_kwargs: dict[str, object] = {}

    def run(self, transport: str, **kwargs: object) -> None:
        self.ran_with = transport
        self.run_kwargs = kwargs


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
    assert "resource_inline_threshold_bytes" not in captured
    assert "resource_max_entries" not in captured
    assert "resource_max_bytes" not in captured
    assert "resource_ttl_s" not in captured
    assert "vlm_cache_path" not in captured
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
        ("--mcp-resource-inline-threshold-kb", "64", "resource_inline_threshold_bytes", 64 * 1024),
        ("--mcp-resource-max-entries", "50", "resource_max_entries", 50),
        ("--mcp-resource-max-mb", "10", "resource_max_bytes", 10 * 1024 * 1024),
        ("--mcp-resource-ttl-s", "60", "resource_ttl_s", 60),
        ("--mcp-rate-limit-count", "5", "rate_limit_count", 5),
        ("--mcp-rate-limit-window-s", "30", "rate_limit_window_s", 30),
    ],
)
def test_numeric_flags_reach_build_server(
    monkeypatch: pytest.MonkeyPatch, flag: str, value: str, kwarg: str, expected: int
) -> None:
    captured = _capture_build_server(monkeypatch)

    code = mcp_cli_module.main([flag, value])

    assert code == 0
    assert captured[kwarg] == expected


def test_mcp_vlm_cache_flag_reaches_build_server_as_a_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    from pathlib import Path

    captured = _capture_build_server(monkeypatch)
    cache_path = tmp_path / "vlm-cache.json"

    code = mcp_cli_module.main(["--mcp-vlm-cache", str(cache_path)])

    assert code == 0
    assert captured["vlm_cache_path"] == Path(cache_path)


def test_mcp_vlm_cache_without_vlm_extra_is_missing_dependency() -> None:
    """Same subprocess-poisoning technique as
    test_vlm_provider_openai_without_vlm_direct_extra_is_missing_dependency
    above — build_server()'s own lazy FileCacheBackend import (guarded by
    refigure.vlm's [vlm] requirement) must route through _exit_code_for,
    not escape as a raw traceback, now that build_server() is inside
    main()'s try/except."""
    import subprocess
    import sys

    from tests.support import REPO_ROOT

    script = (
        "import sys\n"
        "sys.modules['pdfplumber'] = None\n"
        "from refigure.mcp.cli import main\n"
        "sys.exit(main(['--mcp-vlm-cache', '/tmp/refigure-mcp-test-cache.json']))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=30,
    )
    assert result.returncode == EXIT_MISSING_DEPENDENCY
    assert "refigure[vlm]" in result.stderr


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


# --- phase 3: --transport http / auth flags ---------------------------------


def test_transport_http_without_token_file_fails_fast(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        mcp_cli_module.main(["--transport", "http"])

    assert exc_info.value.code == 2
    assert "--mcp-auth-token-file" in capsys.readouterr().err


def test_token_file_without_transport_http_fails_fast(
    capsys: pytest.CaptureFixture[str], tmp_path
) -> None:
    token_file = tmp_path / "tokens.txt"
    token_file.write_text("tok1 = alice\n")

    with pytest.raises(SystemExit) as exc_info:
        mcp_cli_module.main(["--mcp-auth-token-file", str(token_file)])

    assert exc_info.value.code == 2
    assert "--transport http" in capsys.readouterr().err


def test_malformed_token_file_exits_usage_not_internal_error(
    capsys: pytest.CaptureFixture[str], tmp_path
) -> None:
    token_file = tmp_path / "tokens.txt"
    token_file.write_text("this line has no equals sign\n")

    code = mcp_cli_module.main(["--transport", "http", "--mcp-auth-token-file", str(token_file)])

    assert code == EXIT_USAGE
    err = capsys.readouterr().err
    assert "expected exactly one" in err
    assert "internal error" not in err


def test_missing_token_file_exits_usage_not_a_raw_traceback(
    capsys: pytest.CaptureFixture[str], tmp_path
) -> None:
    """Regression: load_token_file()'s Path.read_text() raises
    FileNotFoundError (an OSError, not a ValueError) for a nonexistent
    path — the except clause around it must catch that too, not just
    ValueError, or a plain --mcp-auth-token-file typo crashes with a raw
    Python traceback and exit code 1 instead of this clean EXIT_USAGE
    path (found by ultrareview on this PR)."""
    missing_path = tmp_path / "does-not-exist.txt"

    code = mcp_cli_module.main(["--transport", "http", "--mcp-auth-token-file", str(missing_path)])

    assert code == EXIT_USAGE
    err = capsys.readouterr().err
    assert "error:" in err
    assert "Traceback" not in err
    assert "internal error" not in err


def test_valid_token_file_reaches_build_server_as_token_map(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    captured = _capture_build_server(monkeypatch)
    token_file = tmp_path / "tokens.txt"
    token_file.write_text("tok1 = alice\ntok2 = bob\n")

    code = mcp_cli_module.main(["--transport", "http", "--mcp-auth-token-file", str(token_file)])

    assert code == 0
    assert captured["token_map"] == {"tok1": "alice", "tok2": "bob"}
    assert captured["transport"] == "http"


def test_transport_http_maps_to_the_sdk_streamable_http_literal(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    captured = _capture_build_server(monkeypatch)
    token_file = tmp_path / "tokens.txt"
    token_file.write_text("tok1 = alice\n")

    code = mcp_cli_module.main(["--transport", "http", "--mcp-auth-token-file", str(token_file)])

    assert code == 0
    server = captured["_server"]
    assert isinstance(server, _FakeServer)
    assert server.ran_with == "streamable-http"  # never the bare "http" this CLI accepts
    assert server.run_kwargs["host"] == "127.0.0.1"
    assert server.run_kwargs["port"] == 8000


def test_transport_http_sizes_max_request_body_size_off_the_input_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    captured = _capture_build_server(monkeypatch)
    token_file = tmp_path / "tokens.txt"
    token_file.write_text("tok1 = alice\n")

    code = mcp_cli_module.main(
        [
            "--transport",
            "http",
            "--mcp-auth-token-file",
            str(token_file),
            "--mcp-max-input-mb",
            "10",
        ]
    )

    assert code == 0
    server = captured["_server"]
    assert isinstance(server, _FakeServer)
    assert server.run_kwargs["max_request_body_size"] == 10 * 1024 * 1024 + 4096


def test_transport_http_default_input_cap_sizes_the_request_body_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Same as the test above but with --mcp-max-input-mb left UNSET —
    the formula must fall back to DEFAULT_MAX_INPUT_B64_MB (100), not a
    second, potentially drifted literal."""
    from refigure.mcp.server import DEFAULT_MAX_INPUT_B64_MB

    captured = _capture_build_server(monkeypatch)
    token_file = tmp_path / "tokens.txt"
    token_file.write_text("tok1 = alice\n")

    code = mcp_cli_module.main(["--transport", "http", "--mcp-auth-token-file", str(token_file)])

    assert code == 0
    server = captured["_server"]
    assert isinstance(server, _FakeServer)
    assert (
        server.run_kwargs["max_request_body_size"] == DEFAULT_MAX_INPUT_B64_MB * 1024 * 1024 + 4096
    )


def test_non_loopback_http_host_prints_a_warning(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path
) -> None:
    _capture_build_server(monkeypatch)
    token_file = tmp_path / "tokens.txt"
    token_file.write_text("tok1 = alice\n")

    code = mcp_cli_module.main(
        [
            "--transport",
            "http",
            "--mcp-auth-token-file",
            str(token_file),
            "--mcp-http-host",
            "0.0.0.0",  # noqa: S104 - the value under test, never actually bound
        ]
    )

    assert code == 0
    err = capsys.readouterr().err
    assert "non-loopback" in err
    assert "0.0.0.0" in err


def test_loopback_http_host_prints_no_warning(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path
) -> None:
    _capture_build_server(monkeypatch)
    token_file = tmp_path / "tokens.txt"
    token_file.write_text("tok1 = alice\n")

    code = mcp_cli_module.main(["--transport", "http", "--mcp-auth-token-file", str(token_file)])

    assert code == 0
    assert capsys.readouterr().err == ""

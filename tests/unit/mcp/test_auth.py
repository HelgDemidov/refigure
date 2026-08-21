"""``refigure/mcp/auth.py`` — token-file loading and ``_StaticTokenVerifier``
(phase-3 spec §2)."""

from __future__ import annotations

import pytest

from refigure.mcp.auth import _StaticTokenVerifier, load_token_file

pytestmark = pytest.mark.anyio


def test_load_token_file_parses_a_valid_file(tmp_path) -> None:
    path = tmp_path / "tokens.txt"
    path.write_text("tok1 = alice\ntok2 = bob\n")

    assert load_token_file(path) == {"tok1": "alice", "tok2": "bob"}


def test_load_token_file_skips_blank_lines(tmp_path) -> None:
    path = tmp_path / "tokens.txt"
    path.write_text("\ntok1 = alice\n\n\ntok2 = bob\n\n")

    assert load_token_file(path) == {"tok1": "alice", "tok2": "bob"}


def test_load_token_file_strips_whitespace_case_sensitively(tmp_path) -> None:
    path = tmp_path / "tokens.txt"
    path.write_text("  tok1   =   Alice  \n")

    assert load_token_file(path) == {"tok1": "Alice"}


def test_load_token_file_rejects_a_line_with_no_equals(tmp_path) -> None:
    path = tmp_path / "tokens.txt"
    path.write_text("not-a-valid-line\n")

    with pytest.raises(ValueError, match="expected exactly one"):
        load_token_file(path)


def test_load_token_file_rejects_a_line_with_two_equals(tmp_path) -> None:
    path = tmp_path / "tokens.txt"
    path.write_text("tok1 = alice = extra\n")

    with pytest.raises(ValueError, match="expected exactly one"):
        load_token_file(path)


def test_load_token_file_rejects_an_empty_token(tmp_path) -> None:
    path = tmp_path / "tokens.txt"
    path.write_text(" = alice\n")

    with pytest.raises(ValueError, match="non-empty"):
        load_token_file(path)


def test_load_token_file_rejects_an_empty_caller_id(tmp_path) -> None:
    path = tmp_path / "tokens.txt"
    path.write_text("tok1 = \n")

    with pytest.raises(ValueError, match="non-empty"):
        load_token_file(path)


def test_load_token_file_rejects_local_caller_id_as_reserved(tmp_path) -> None:
    path = tmp_path / "tokens.txt"
    path.write_text("tok1 = __local__\n")

    with pytest.raises(ValueError, match="reserved"):
        load_token_file(path)


def test_load_token_file_rejects_one_token_on_two_different_caller_ids(tmp_path) -> None:
    path = tmp_path / "tokens.txt"
    path.write_text("tok1 = alice\ntok1 = bob\n")

    with pytest.raises(ValueError, match="different caller_id"):
        load_token_file(path)


def test_load_token_file_allows_the_same_token_and_caller_id_twice(tmp_path) -> None:
    # An idempotent duplicate line, not a typo — no error.
    path = tmp_path / "tokens.txt"
    path.write_text("tok1 = alice\ntok1 = alice\n")

    assert load_token_file(path) == {"tok1": "alice"}


def test_load_token_file_allows_rotation_different_tokens_one_caller_id(tmp_path) -> None:
    path = tmp_path / "tokens.txt"
    path.write_text("tok-old = alice\ntok-new = alice\n")

    token_map = load_token_file(path)

    assert token_map == {"tok-old": "alice", "tok-new": "alice"}


def test_load_token_file_error_message_includes_the_line_number(tmp_path) -> None:
    path = tmp_path / "tokens.txt"
    path.write_text("tok1 = alice\nbroken-line\n")

    with pytest.raises(ValueError, match=r":2:"):
        load_token_file(path)


async def test_verifier_returns_access_token_on_a_matching_token() -> None:
    verifier = _StaticTokenVerifier({"tok1": "alice", "tok2": "bob"})

    result = await verifier.verify_token("tok2")

    assert result is not None
    assert result.client_id == "bob"
    assert result.token == "tok2"
    assert result.scopes == []


async def test_verifier_returns_none_on_a_non_matching_token() -> None:
    verifier = _StaticTokenVerifier({"tok1": "alice"})

    assert await verifier.verify_token("not-configured") is None


async def test_verifier_uses_constant_time_comparison(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    import refigure.mcp.auth as auth_module

    real_compare_digest = auth_module.hmac.compare_digest

    def spying_compare_digest(a: str, b: str) -> bool:
        calls.append((a, b))
        return real_compare_digest(a, b)

    monkeypatch.setattr(auth_module.hmac, "compare_digest", spying_compare_digest)
    verifier = _StaticTokenVerifier({"tok1": "alice", "tok2": "bob"})

    await verifier.verify_token("tok2")

    # A dict-lookup implementation would never call compare_digest at all —
    # this asserts the actual mechanism used, not just the outcome.
    assert ("tok2", "tok1") in calls
    assert ("tok2", "tok2") in calls

"""``tests/unit/test_cli.py`` — ``refigure.cli``, the argparse-based console
entry point (``docs/cli/cli-wrapper/cli-wrapper-2026-08-05.md``). Synthetic docx/xlsx
fixtures (``build_minimal_docx`` / a plain ``openpyxl.Workbook()``) for
speed, same convention as ``test_robustness.py`` — real-corpus, end-to-end
coverage lives in the integration smoke test (full 27-fixture batch)."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import openpyxl
import pytest

from refigure.cli import (
    EXIT_BATCH_PARTIAL_FAILURE,
    EXIT_CORRUPT_ARCHIVE,
    EXIT_INTERNAL_ERROR,
    EXIT_MISSING_DEPENDENCY,
    EXIT_OK,
    EXIT_UNSUPPORTED_FORMAT,
    EXIT_USAGE,
    main,
)
from tests.support import REPO_ROOT

from .docx.test_docx import build_minimal_docx


def _write_docx(path: Path, paragraphs: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_minimal_docx(paragraphs))
    return path


def _write_xlsx(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    assert wb.active is not None
    wb.active.append(["a", "b"])
    buf = io.BytesIO()
    wb.save(buf)
    path.write_bytes(buf.getvalue())
    return path


class TestSingleFile:
    def test_stdout_by_default(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        doc = _write_docx(tmp_path / "doc.docx", ["Hello world"])
        assert main([str(doc)]) == EXIT_OK
        assert "Hello world" in capsys.readouterr().out

    def test_output_file_not_stdout(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        doc = _write_docx(tmp_path / "doc.docx", ["Hello world"])
        out_file = tmp_path / "out.md"
        assert main([str(doc), "-o", str(out_file)]) == EXIT_OK
        assert "Hello world" in out_file.read_text(encoding="utf-8")
        assert capsys.readouterr().out == ""

    def test_missing_file_is_usage_error(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit) as exc:
            main([str(tmp_path / "nope.docx")])
        assert exc.value.code == EXIT_USAGE

    def test_unrecognized_extension_is_usage_error(self, tmp_path: Path) -> None:
        stray = tmp_path / "doc.txt"
        stray.write_text("hi", encoding="utf-8")
        with pytest.raises(SystemExit) as exc:
            main([str(stray)])
        assert exc.value.code == EXIT_USAGE


class TestStdin:
    def test_requires_format(self) -> None:
        with pytest.raises(SystemExit) as exc:
            main([])
        assert exc.value.code == EXIT_USAGE

    def test_reads_and_converts(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        data = build_minimal_docx(["From stdin"])
        monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(data)))
        assert main(["--format", "docx"]) == EXIT_OK
        assert "From stdin" in capsys.readouterr().out


class TestTypedExitCodes:
    def test_unsupported_format(self, tmp_path: Path) -> None:
        bad = tmp_path / "doc.docx"
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("hello.txt", "hi")
        assert main([str(bad)]) == EXIT_UNSUPPORTED_FORMAT

    def test_corrupt_archive(self, tmp_path: Path) -> None:
        bad = tmp_path / "doc.docx"
        bad.write_bytes(b"not a zip at all")
        assert main([str(bad)]) == EXIT_CORRUPT_ARCHIVE

    def test_missing_optional_dependency_via_subprocess(self, tmp_path: Path) -> None:
        # sys.modules poisoning only prevents the FIRST import of openpyxl in
        # a process — needs a fresh subprocess, same technique and reasoning
        # as test_optional_dependency_guards.py.
        xlsx_path = _write_xlsx(tmp_path / "doc.xlsx")
        script = (
            "import sys\n"
            "sys.modules['openpyxl'] = None\n"
            "from refigure.cli import main\n"
            f"sys.exit(main([{str(xlsx_path)!r}]))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            timeout=30,
        )
        assert result.returncode == EXIT_MISSING_DEPENDENCY
        assert "refigure[xlsx]" in result.stderr

    def test_unexpected_exception_is_internal_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        doc = _write_docx(tmp_path / "doc.docx", ["hi"])

        import refigure.docx as docx_module

        def _boom(*args: object, **kwargs: object) -> None:
            raise RuntimeError("boom")

        monkeypatch.setattr(docx_module, "convert", _boom)
        assert main([str(doc)]) == EXIT_INTERNAL_ERROR
        assert "internal error" in capsys.readouterr().err


class TestBatchMode:
    def test_requires_output_dir(self, tmp_path: Path) -> None:
        a = _write_docx(tmp_path / "a.docx", ["A"])
        b = _write_docx(tmp_path / "b.docx", ["B"])
        with pytest.raises(SystemExit) as exc:
            main([str(a), str(b)])
        assert exc.value.code == EXIT_USAGE

    def test_single_directory_triggers_batch(self, tmp_path: Path) -> None:
        _write_docx(tmp_path / "a.docx", ["A"])
        out_dir = tmp_path / "out"
        assert main([str(tmp_path), "-o", str(out_dir)]) == EXIT_OK
        assert (out_dir / "a.md").exists()

    def test_keep_going_partial_failure_summary_and_exit1(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        good = _write_docx(tmp_path / "good.docx", ["Good"])
        bad = tmp_path / "bad.docx"
        bad.write_bytes(b"not a zip at all")
        out_dir = tmp_path / "out"

        code = main([str(good), str(bad), "-o", str(out_dir)])

        assert code == EXIT_BATCH_PARTIAL_FAILURE
        assert (out_dir / "good.md").exists()
        assert not (out_dir / "bad.md").exists()
        assert "1/2 converted, 1 failed" in capsys.readouterr().err

    def test_fail_fast_aborts_before_writing_anything(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.docx"
        bad.write_bytes(b"not a zip at all")
        good = _write_docx(tmp_path / "good.docx", ["Good"])
        out_dir = tmp_path / "out"

        code = main([str(bad), str(good), "-o", str(out_dir), "--fail-fast"])

        assert code == EXIT_CORRUPT_ARCHIVE
        assert not out_dir.exists() or not any(out_dir.iterdir())

    def test_preserves_relative_structure_no_collision(self, tmp_path: Path) -> None:
        # anti-Docling-#3811 regression: same basename under 2 different
        # subdirectories of one walked root must not collide.
        _write_docx(tmp_path / "a" / "x.docx", ["A version"])
        _write_docx(tmp_path / "b" / "x.docx", ["B version"])
        out_dir = tmp_path / "out"

        assert main([str(tmp_path), "-o", str(out_dir)]) == EXIT_OK

        assert "A version" in (out_dir / "a" / "x.md").read_text(encoding="utf-8")
        assert "B version" in (out_dir / "b" / "x.md").read_text(encoding="utf-8")

    def test_explicit_collision_is_usage_error_and_writes_nothing(self, tmp_path: Path) -> None:
        first = _write_docx(tmp_path / "a" / "x.docx", ["A"])
        second = _write_docx(tmp_path / "b" / "x.docx", ["B"])
        out_dir = tmp_path / "out"

        code = main([str(first), str(second), "-o", str(out_dir)])

        assert code == EXIT_USAGE
        assert not out_dir.exists() or not any(out_dir.rglob("*.md"))


class TestJsonFlag:
    def test_single_file_is_valid_json_with_expected_keys(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        doc = _write_docx(tmp_path / "doc.docx", ["Hello"])
        assert main([str(doc), "--json"]) == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert set(payload) == {
            "markdown",
            "warnings",
            "charts_found",
            "charts_rendered",
            "groups_found",
            "vlm_used",
        }
        assert "Hello" in payload["markdown"]

    def test_batch_writes_dot_json_not_dot_md(self, tmp_path: Path) -> None:
        _write_docx(tmp_path / "doc.docx", ["Hello"])
        out_dir = tmp_path / "out"
        assert main([str(tmp_path), "-o", str(out_dir), "--json"]) == EXIT_OK
        assert (out_dir / "doc.json").exists()
        assert not (out_dir / "doc.md").exists()
        json.loads((out_dir / "doc.json").read_text(encoding="utf-8"))  # parses cleanly


class TestVerbosityFlags:
    def test_default_shows_warnings_on_stderr(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        empty_doc = _write_docx(tmp_path / "empty.docx", [])
        assert main([str(empty_doc)]) == EXIT_OK
        assert "warning: no extractable content" in capsys.readouterr().err

    def test_quiet_suppresses_warnings_not_stdout(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        empty_doc = _write_docx(tmp_path / "empty.docx", [])
        assert main([str(empty_doc), "-q"]) == EXIT_OK
        captured = capsys.readouterr()
        assert "warning:" not in captured.err

    def test_batch_quiet_still_prints_summary(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_docx(tmp_path / "empty.docx", [])
        out_dir = tmp_path / "out"
        assert main([str(tmp_path), "-o", str(out_dir), "-q"]) == EXIT_OK
        captured = capsys.readouterr()
        assert "warning:" not in captured.err
        assert "1/1 converted, 0 failed" in captured.err

    def test_verbose_adds_per_file_batch_progress(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_docx(tmp_path / "a.docx", ["A"])
        out_dir = tmp_path / "out"
        assert main([str(tmp_path), "-o", str(out_dir), "-v"]) == EXIT_OK
        assert "converted:" in capsys.readouterr().err

    def test_default_batch_has_no_per_file_progress(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_docx(tmp_path / "a.docx", ["A"])
        out_dir = tmp_path / "out"
        assert main([str(tmp_path), "-o", str(out_dir)]) == EXIT_OK
        assert "converted:" not in capsys.readouterr().err

    def test_verbose_and_quiet_are_mutually_exclusive(self, tmp_path: Path) -> None:
        doc = _write_docx(tmp_path / "doc.docx", ["A"])
        with pytest.raises(SystemExit) as exc:
            main([str(doc), "-v", "-q"])
        assert exc.value.code == EXIT_USAGE


class TestMiscFlags:
    def test_version_prints_and_exits_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc:
            main(["--version"])
        assert exc.value.code == 0
        assert "refigure" in capsys.readouterr().out

    def test_strict_flag_is_accepted_and_does_not_crash(self, tmp_path: Path) -> None:
        doc = _write_docx(tmp_path / "doc.docx", ["Hello"])
        assert main([str(doc), "--strict"]) == EXIT_OK

"""``refigure`` console command — thin wrapper over ``refigure.docx.convert()``/
``refigure.xlsx.convert()``. Design rationale: ``docs/cli-wrapper-2026-08-05.md``.

Format isolation extends to the CLI: this module must not import
``refigure.docx``/``refigure.xlsx`` at module level. A bare (or single-extra)
install must still be able to run ``refigure --help``/``--version`` without
every format's heavy dependency installed — the same guard-ordering
discipline ``xlsx.py``/``docx.py`` already apply at the library level (see
their own module docstrings), applied here by keeping the per-format import
lazy, deferred to the moment a specific format is actually needed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Protocol

from ._io import Source
from .api import Config, ConversionResult

EXIT_OK = 0
EXIT_BATCH_PARTIAL_FAILURE = 1
EXIT_USAGE = 2
EXIT_UNSUPPORTED_FORMAT = 3
EXIT_CORRUPT_ARCHIVE = 4
EXIT_MISSING_DEPENDENCY = 5
EXIT_INTERNAL_ERROR = 6

_SUFFIX_TO_FORMAT = {".docx": "docx", ".xlsx": "xlsx"}


class _ConvertFn(Protocol):
    """Structural type of ``refigure.docx.convert``/``refigure.xlsx.convert``
    — lets ``_convert_fn`` return a plain callable (mypy-checkable) instead
    of an untyped module object."""

    def __call__(self, source: Source, *, config: Config | None = None) -> ConversionResult: ...


def _format_for_path(path: Path) -> str | None:
    return _SUFFIX_TO_FORMAT.get(path.suffix.lower())


def _convert_fn(fmt: str) -> _ConvertFn:
    """Lazy, per-format import — kept out of module scope on purpose (see
    module docstring)."""
    if fmt == "docx":
        from . import docx

        return docx.convert
    if fmt == "xlsx":
        from . import xlsx

        return xlsx.convert
    raise ValueError(f"unknown format: {fmt!r}")  # pragma: no cover - argparse choices guards this


def _exit_code_for(exc: Exception) -> int:
    # Imported lazily too — importing refigure.api at module level is cheap
    # (lxml-only, see api.py), but the exception CLASSES only need to exist
    # once we actually have an exception to classify.
    from .api import CorruptArchiveError, MissingOptionalDependencyError, UnsupportedFormatError

    if isinstance(exc, UnsupportedFormatError):
        return EXIT_UNSUPPORTED_FORMAT
    if isinstance(exc, CorruptArchiveError):
        return EXIT_CORRUPT_ARCHIVE
    if isinstance(exc, MissingOptionalDependencyError):
        return EXIT_MISSING_DEPENDENCY
    return EXIT_INTERNAL_ERROR


def _convert_one(
    source: Source, fmt: str, config: Config
) -> tuple[ConversionResult | None, int, str | None]:
    """Run one conversion, translating refigure's typed exceptions (plus a
    safety net for anything else) into ``(result, exit_code, message)``
    instead of letting an exception escape to a bare traceback — the gap
    found in every one of the 3 competitor CLIs researched for the spec."""
    try:
        convert_fn = _convert_fn(fmt)
        return convert_fn(source, config=config), EXIT_OK, None
    except Exception as exc:
        code = _exit_code_for(exc)
        message = str(exc) if code != EXIT_INTERNAL_ERROR else f"internal error: {exc}"
        return None, code, message


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="refigure",
        description="Convert DOCX/XLSX to Markdown, preserving native charts.",
    )
    parser.add_argument(
        "source",
        nargs="?",
        metavar="SOURCE",
        help="File to convert. Omit to read from stdin (requires --format).",
    )
    parser.add_argument(
        "-o", "--output", metavar="FILE", help="Write markdown to FILE instead of stdout."
    )
    parser.add_argument(
        "--format",
        choices=sorted(_SUFFIX_TO_FORMAT.values()),
        help="Format hint, required when reading from stdin.",
    )
    return parser


def _write_stdout(text: str) -> None:
    # Explicit UTF-8, never relying on sys.stdout.encoding being non-None —
    # a redirected/CI stdout can leave it unset (an open, unmerged fragility
    # in one of the 3 researched competitor CLIs, per the spec).
    stdout = sys.stdout
    if hasattr(stdout, "reconfigure"):
        try:
            stdout.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):  # pragma: no cover - best effort only
            pass
    stdout.write(text)


def _emit(markdown: str, output: str | None) -> None:
    if output is None:
        _write_stdout(markdown)
    else:
        Path(output).write_text(markdown, encoding="utf-8")


def _report_warnings(warnings: list[str]) -> None:
    # stdout is reserved for markdown alone — warnings always go to stderr.
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = Config()

    source: Source
    fmt: str
    if args.source is None:
        if args.format is None:
            parser.error("--format is required when reading from stdin")
        source = sys.stdin.buffer.read()
        fmt = args.format
    else:
        path = Path(args.source)
        if not path.is_file():
            parser.error(f"{args.source}: no such file")
        detected = _format_for_path(path)
        if detected is None:
            parser.error(f"{args.source}: unrecognized extension (expected .docx or .xlsx)")
        source = path
        fmt = detected

    result, code, message = _convert_one(source, fmt, config)
    if result is None:
        print(f"error: {message}", file=sys.stderr)
        return code

    _report_warnings(result.warnings)
    _emit(result.markdown, args.output)
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - exercised via __main__.py/console script
    sys.exit(main())

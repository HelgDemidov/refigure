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
        "sources",
        nargs="*",
        metavar="SOURCE",
        help=(
            "File(s) and/or director(y/ies) to convert. 2+ sources or a "
            "directory triggers batch mode (requires -o DIR). Omit entirely "
            "to read a single document from stdin (requires --format)."
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        metavar="PATH",
        help="Single-source mode: output file. Batch mode: output directory (required).",
    )
    parser.add_argument(
        "--format",
        choices=sorted(_SUFFIX_TO_FORMAT.values()),
        help="Format hint, required when reading from stdin.",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Batch mode: abort on the first failed source instead of continuing.",
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


def _run_stdin(args: argparse.Namespace, config: Config, parser: argparse.ArgumentParser) -> int:
    if args.format is None:
        parser.error("--format is required when reading from stdin")
    data = sys.stdin.buffer.read()
    result, code, message = _convert_one(data, args.format, config)
    if result is None:
        print(f"error: {message}", file=sys.stderr)
        return code
    _report_warnings(result.warnings)
    _emit(result.markdown, args.output)
    return EXIT_OK


def _run_single(
    path: Path, args: argparse.Namespace, config: Config, parser: argparse.ArgumentParser
) -> int:
    if not path.is_file():
        parser.error(f"{path}: no such file")
    fmt = _format_for_path(path)
    if fmt is None:
        parser.error(f"{path}: unrecognized extension (expected .docx or .xlsx)")

    result, code, message = _convert_one(path, fmt, config)
    if result is None:
        print(f"error: {message}", file=sys.stderr)
        return code
    _report_warnings(result.warnings)
    _emit(result.markdown, args.output)
    return EXIT_OK


def _resolve_batch_sources(raw_sources: list[str], parser: argparse.ArgumentParser) -> list[Path]:
    """Validate every positional argument up front (exists, and — for a
    plain file — a recognized extension) so ``_plan_batch`` can assume
    well-formed input and stay pure. Mirrors ``_run_single``'s validation,
    for the same invocation-is-well-formed class of check."""
    resolved: list[Path] = []
    for raw in raw_sources:
        path = Path(raw)
        if path.is_dir():
            resolved.append(path)
        elif path.is_file():
            if _format_for_path(path) is None:
                parser.error(f"{raw}: unrecognized extension (expected .docx or .xlsx)")
            resolved.append(path)
        else:
            parser.error(f"{raw}: no such file or directory")
    return resolved


def _plan_batch(sources: list[Path]) -> list[tuple[Path, Path]]:
    """(input file, output-relative-path-with-.md-suffix source) pairs. A
    directory source contributes every ``.docx``/``.xlsx`` file under it,
    recursively, with its output path relative to that directory (preserves
    structure — Docling issue #3811 avoided by construction, see spec §3).
    A plain file source contributes itself, relative-pathed by its own
    basename. Duplicate *input* paths (the same directory passed twice, or a
    file reachable both directly and via a directory walk) are silently
    deduplicated, keeping the first occurrence — not a collision."""
    plan: list[tuple[Path, Path]] = []
    seen_inputs: set[Path] = set()
    for src in sources:
        entries: list[tuple[Path, Path]]
        if src.is_dir():
            entries = [
                (f, f.relative_to(src))
                for f in sorted(src.rglob("*"))
                if f.is_file() and _format_for_path(f) is not None
            ]
        else:
            entries = [(src, Path(src.name))]
        for file_path, rel in entries:
            resolved = file_path.resolve()
            if resolved in seen_inputs:
                continue
            seen_inputs.add(resolved)
            plan.append((file_path, rel))
    return plan


def _detect_collisions(plan: list[tuple[Path, Path]]) -> dict[Path, list[Path]]:
    """Distinct input files that would map to the identical output path
    (e.g. two sources both containing an ``x.docx`` at their root) — the
    general form of Docling issue #3811, not just the directory-walk case
    §3 already avoids structurally."""
    by_output: dict[Path, list[Path]] = {}
    for file_path, rel in plan:
        by_output.setdefault(rel.with_suffix(".md"), []).append(file_path)
    return {out: srcs for out, srcs in by_output.items() if len(srcs) > 1}


def _run_batch(
    sources: list[Path],
    args: argparse.Namespace,
    config: Config,
    parser: argparse.ArgumentParser,
) -> int:
    if args.output is None:
        parser.error("-o/--output DIR is required in batch mode (2+ sources or a directory)")

    plan = _plan_batch(sources)
    if not plan:
        parser.error("no .docx/.xlsx files found among the given sources")

    collisions = _detect_collisions(plan)
    if collisions:
        for out, srcs in sorted(collisions.items()):
            names = ", ".join(str(s) for s in srcs)
            print(f"error: {out} would be written by multiple sources: {names}", file=sys.stderr)
        return EXIT_USAGE

    out_root = Path(args.output)
    converted = 0
    failures: list[tuple[Path, str]] = []
    for file_path, rel in plan:
        fmt = _format_for_path(file_path)
        if fmt is None:  # pragma: no cover - _resolve_batch_sources/_plan_batch already filter
            continue

        result, code, message = _convert_one(file_path, fmt, config)
        if result is None:
            print(f"error: {file_path}: {message}", file=sys.stderr)
            if args.fail_fast:
                return code
            failures.append((file_path, message or ""))
            continue

        for warning in result.warnings:
            print(f"warning: {file_path}: {warning}", file=sys.stderr)
        out_path = out_root / rel.with_suffix(".md")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(result.markdown, encoding="utf-8")
        converted += 1

    total = len(plan)
    print(f"{converted}/{total} converted, {len(failures)} failed", file=sys.stderr)
    return EXIT_BATCH_PARTIAL_FAILURE if failures else EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = Config()

    if not args.sources:
        return _run_stdin(args, config, parser)

    raw_paths = [Path(s) for s in args.sources]
    is_batch = len(raw_paths) > 1 or raw_paths[0].is_dir()
    if is_batch:
        sources = _resolve_batch_sources(args.sources, parser)
        return _run_batch(sources, args, config, parser)

    return _run_single(raw_paths[0], args, config, parser)


if __name__ == "__main__":  # pragma: no cover - exercised via __main__.py/console script
    sys.exit(main())

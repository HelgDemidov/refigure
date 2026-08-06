"""``ChartData`` -> markdown (spec chart-data-extraction §3): a GFM table is
produced ALWAYS (lossless), mermaid is ADDITIONAL — only for chart kinds that
objectively HAVE a mermaid construct (a limit of mermaid's own vocabulary,
not caution on our part): ``pie``/``doughnut``->``pie``, ``column``/``bar``/
``line``/``area``/``combo``->``xychart-beta`` (bar+line combo = an overlay of
two series of different kind), ``radar``->``radar-beta``. Everything else
(``scatter``, stacked bar, bubble/waterfall/treemap/sunburst/boxplot/3D/log
scales) -> there is NO mermaid construct at all, table only.
``verify+fallback``: if the shape is risky (series/category length mismatch,
gaps, >1 series for a pie) -> mermaid is dropped, the table is always kept
(see the spec's Design rationale).

Two-level mermaid verification before output (user decision 2026-07-22, an
evolution of the same verify+fallback): the structural heuristics above
catch a risky SHAPE, but don't guarantee mermaid.js will actually accept the
SYNTAX/SEMANTICS of the result — a real example: quotes inside ``pie title``
are syntactically valid but render literally. The final gate is
``mermaid_renders``: an actual render through ``mermaidx`` (embedded
QuickJS, a runtime dependency since 2026-07-22) — any failure -> fall back
to table-only, NOT a conversion crash. The gate is public (spec
convert-knowledge-seam-hardening §8-bis): ``figures_vlm`` applies it to
mermaid fences in the VLM response — same class of output, same discipline
(publication precedent — ``apply_superseded_gate``)."""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import cast

from .chart_data import ChartData

logger = logging.getLogger(__name__)

# mermaidx is an optional per-format extra (refigure [docx]/[xlsx]) — a
# pdf/html-only install must not require it.
try:
    import mermaidx
except ImportError:
    mermaidx = None


@lru_cache(maxsize=1)
def _warn_missing_mermaidx() -> None:
    logger.warning(
        "mermaidx not installed — chart diagrams disabled, falling back to "
        "tables only (install refigure[docx]/[xlsx] to enable rendering)"
    )


def mermaidx_available() -> bool:
    """Whether the optional ``mermaidx`` dependency is installed.

    A proper accessor, not direct access to the module-level ``mermaidx``
    name from other modules — the conditional ``try/except ImportError``
    import isn't something mypy's strict re-export checking resolves cleanly
    across module boundaries (refigure.docx/refigure.xlsx need this to build
    ``ConversionResult.warnings``, see stage2-public-api-wrapper spec §3)."""
    return mermaidx is not None


_STRIP_RE = re.compile(r"[\[\]{}()]")
_SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")

_PIE_LIKE = frozenset({"pie", "doughnut"})
_XYCHART_LIKE = frozenset({"column", "bar", "line", "area", "combo"})


def _sanitize_label(text: str) -> str:
    """Real bug from convert-xlsx (VLM era): brackets/quotes in a label break
    the mermaid parser — strip out ``[]{}()``, replace double quotes with
    single quotes (labels are always double-quoted in the output)."""
    cleaned = _STRIP_RE.sub("", text).replace('"', "'").strip()
    return cleaned or "?"


def _slug(text: str, fallback_idx: int) -> str:
    """A bare word with no spaces/punctuation — the radar-beta node id
    (``curve id[...]``): mermaid requires exactly this kind of id BEFORE the
    label."""
    s = _SLUG_RE.sub("_", text).strip("_").lower()
    if not s or not s[0].isalpha():
        s = f"s{fallback_idx}{s}"
    return s


def _fmt_num(v: float) -> str:
    return f"{v:.4g}"


def _count_decimal_digits(fmt: str) -> int:
    if "." not in fmt:
        return 0
    frac = re.split(r"[;\s]", fmt.split(".", 1)[1])[0]
    return sum(1 for ch in frac if ch in "0#")


def _format_value(value: float | None, value_format: str | None) -> str:
    """Pragmatic formatting (§3): a ``0.0%``-style format -> multiply by 100
    and append "%", with the decimal count taken from the format code; an
    unknown/missing format -> round to ~4 significant digits (NOT a raw
    float — real bug from govtech: ``0.58909698401216537`` with no format is
    unreadable)."""
    if value is None:
        return ""
    if value_format:
        if "%" in value_format:
            decimals = _count_decimal_digits(value_format.split("%", 1)[0])
            return f"{value * 100:.{decimals}f}%"
        if re.search(r"[0#]", value_format):
            decimals = _count_decimal_digits(value_format)
            return f"{value:,.{decimals}f}"
    return _fmt_num(value)


def _caption(data: ChartData) -> str | None:
    parts = [p for p in (data.title, data.value_axis_title) if p]
    return " — ".join(parts) if parts else None


def _row_labels(data: ChartData) -> tuple[str, ...]:
    if data.categories:
        return data.categories
    max_len = max((len(s.values) for s in data.series), default=0)
    return tuple(str(i + 1) for i in range(max_len))


def _table(data: ChartData) -> str:
    header = ["Category"] + [s.name or f"Series {i + 1}" for i, s in enumerate(data.series)]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * len(header)) + " |",
    ]
    for i, label in enumerate(_row_labels(data)):
        row = [label.replace("\n", " ")] + [
            _format_value(s.values[i] if i < len(s.values) else None, data.value_format)
            for s in data.series
        ]
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _series_shape_ok(data: ChartData) -> bool:
    """The verify part of the verify+fallback-to-table rule: mermaid has no
    way to represent a gap/length mismatch — the table handles this honestly
    (an empty cell), while mermaid is simply dropped entirely."""
    if not data.categories:
        return False
    return all(
        len(s.values) == len(data.categories) and all(v is not None for v in s.values)
        for s in data.series
    )


def _dense(values: tuple[float | None, ...]) -> tuple[float, ...]:
    """``values`` is only passed in AFTER ``_series_shape_ok`` has confirmed
    there are no ``None`` entries — a narrow cast instead of a redundant
    runtime check/filter (filtering would read as "drop the Nones," but the
    invariant already guarantees "there are none")."""
    return cast(tuple[float, ...], values)


def _mermaid_pie(data: ChartData) -> str | None:
    if len(data.series) != 1 or not _series_shape_ok(data):
        return None
    values = _dense(data.series[0].values)
    if any(v < 0 for v in values):
        return None  # a share can't be negative — the shape is risky
    # "pie title <text>" is a FLAT string, WITHOUT quotes (unlike the data
    # labels below, which mermaid requires to be quoted): found via an
    # actual render of the govtech fixture (the user asked for a visual
    # check of the result) — quotes wrapped around the title are NOT
    # interpreted by mermaid as a delimiter, they render literally (the
    # trailing `"` was visible in the SVG). mermaid-parser-bundle/
    # mermaid.parse() passed this shape as valid (quotes inside a flat
    # string aren't a grammar error), so syntax validation alone didn't
    # catch it — only the visual render did.
    lines = [f"pie title {_sanitize_label(data.title)}"] if data.title else ["pie"]
    for cat, v in zip(data.categories, values, strict=True):
        lines.append(f'    "{_sanitize_label(cat)}" : {_fmt_num(v)}')
    return "```mermaid\n" + "\n".join(lines) + "\n```"


def _mermaid_xychart(data: ChartData) -> str | None:
    if data.stacked or not _series_shape_ok(data):
        return None
    lines = ["xychart-beta"]
    x_axis = ", ".join(f'"{_sanitize_label(c)}"' for c in data.categories)
    lines.append(f"x-axis [{x_axis}]")
    y_label = _sanitize_label(data.value_axis_title) if data.value_axis_title else "Value"
    all_values = [v for s in data.series for v in _dense(s.values)]
    y_min, y_max = min(0.0, min(all_values)), max(all_values)
    if y_min == y_max:
        y_max = y_min + 1  # xychart-beta requires a distinguishable axis range
    lines.append(f'y-axis "{y_label}" {_fmt_num(y_min)} --> {_fmt_num(y_max)}')
    for s in data.series:
        kind = "bar" if s.kind in ("bar", "column") else "line"
        values_str = ", ".join(_fmt_num(v) for v in _dense(s.values))
        lines.append(f"{kind} [{values_str}]")
    return "```mermaid\n" + "\n".join(lines) + "\n```"


def _mermaid_radar(data: ChartData) -> str | None:
    if not _series_shape_ok(data):
        return None
    axis = ", ".join(
        f'{_slug(cat, i)}["{_sanitize_label(cat)}"]' for i, cat in enumerate(data.categories)
    )
    lines = [f"axis {axis}"]
    for i, s in enumerate(data.series):
        label = _sanitize_label(s.name) if s.name else f"Series {i + 1}"
        sid = _slug(s.name or f"series{i}", i)
        values_str = ", ".join(_fmt_num(v) for v in _dense(s.values))
        lines.append(f'curve {sid}["{label}"]{{{values_str}}}')
    return "```mermaid\nradar-beta\n" + "\n".join(lines) + "\n```"


def mermaid_renders(code: str) -> bool:
    """An actual render check through ``mermaidx`` (spec
    chart-data-extraction, user decision 2026-07-22 — first a dev-time test,
    then a standing runtime gate): the structural heuristics above
    (``_series_shape_ok`` etc.) catch a RISKY shape, but don't guarantee
    mermaid.js will actually accept the result — a real example (found via a
    visual render of govtech, commit `509f6ff`): ``pie title "T"`` is
    syntactically valid (quotes inside a flat string aren't a grammar
    error), but it renders with literal quotes. Syntax validators
    (``mermaid-parser-bundle``/``mermaid.parse()``) passed this shape
    through — only an actual render distinguishes "grammatically valid"
    from "the real renderer will accept it." Any failure (including the
    library itself being unavailable) -> False, fail-safe — the caller
    simply loses the mermaid block, NOT the whole conversion."""
    if mermaidx is None:
        _warn_missing_mermaidx()
        return False
    try:
        mermaidx.render(code).svg()
        return True
    except Exception:  # noqa: BLE001 — any real-render failure -> an honest fallback to the table
        return False


def _mermaid(data: ChartData) -> str | None:
    if data.chart_type in _PIE_LIKE:
        candidate = _mermaid_pie(data)
    elif data.chart_type in _XYCHART_LIKE:
        candidate = _mermaid_xychart(data)
    elif data.chart_type == "radar":
        candidate = _mermaid_radar(data)
    else:
        return None  # scatter/stacked-bar/etc. — there is no mermaid construct at all
    if candidate is None:
        return None
    code = candidate.removeprefix("```mermaid\n").removesuffix("\n```")
    return candidate if mermaid_renders(code) else None


def render_chart(data: ChartData) -> str | None:
    """None -> extraction is empty (no series/values), the caller invokes the
    caption fallback (an honest marker, see
    ``xlsx_charts.render_chart_marker``/``docx_groups._render_group_marker``).
    Output order (§3): caption -> mermaid (if present) -> table."""
    if not data.series or not any(any(v is not None for v in s.values) for s in data.series):
        return None
    parts = [p for p in (_caption(data), _mermaid(data), _table(data)) if p]
    return "\n\n".join(parts)

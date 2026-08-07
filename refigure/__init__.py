from importlib.metadata import PackageNotFoundError, version

from .api import (
    Config,
    ConversionResult,
    CorruptArchiveError,
    MissingOptionalDependencyError,
    UnsupportedFormatError,
)

try:
    __version__ = version("refigure")
except PackageNotFoundError:
    # Running from a source checkout with no installed distribution to read
    # metadata from (e.g. this project's own dev workflow: pytest imports
    # refigure/ directly from the repo, no `pip install -e .` step) — honest
    # "unknown", not a hardcoded guess at pyproject.toml's current value,
    # which would just reintroduce a second, driftable source of truth.
    __version__ = "0+unknown"

__all__ = [
    "Config",
    "ConversionResult",
    "CorruptArchiveError",
    "MissingOptionalDependencyError",
    "UnsupportedFormatError",
]

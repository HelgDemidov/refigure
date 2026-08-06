"""``python -m refigure`` — equivalent to the installed ``refigure`` console
script (``refigure.cli:main``, registered via ``[project.scripts]``)."""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":  # pragma: no cover - exercised via test_cli.py's subprocess smoke test
    sys.exit(main())

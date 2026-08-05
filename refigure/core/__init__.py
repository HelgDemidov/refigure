"""Core engine — always installed, no format-specific dependency.

``chart_data.py``/``chart_render.py`` (native OOXML chart parsing +
markdown/mermaid rendering, ``lxml``-only + optional ``mermaidx`` inside
``chart_render.py``) and ``zipsafe.py`` (decompression-bomb ceiling for
untrusted zip archives) are shared by both ``refigure.docx`` and
``refigure.xlsx`` — neither depends on mammoth/openpyxl/pdfplumber.
"""

from __future__ import annotations

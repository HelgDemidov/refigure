# refigure

**Converters where figures survive.**

PDF / DOCX / XLSX → Markdown converters that treat embedded charts, composite
diagrams and infographics as single semantic objects instead of silently
dropping or fragmenting them: positioned machine-readable markers as the
zero-loss floor, optional VLM interpretation (prose + mermaid) on top, cached
and reproducible offline.

## Status

Pre-release skeleton. The converters are being extracted from a working
document-analysis pipeline (government AI-policy corpus); first public release
planned for **August 2026** as a single package with per-format extras
(`[pdf]` / `[docx]` / `[xlsx]`).

PyPI names `refigure` and `refigure-md` are reserved (placeholder 0.0.0).

## License

Apache-2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).

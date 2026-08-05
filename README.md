# refigure

**Converters where figures survive.**

DOCX / XLSX → Markdown converters that treat embedded charts, composite
diagrams and infographics as single semantic objects instead of silently
dropping or fragmenting them: native OOXML chart-data extraction (no
rasterize/OCR/VLM) plus positioned machine-readable markers as the zero-loss
floor, optional VLM interpretation (prose + mermaid) on top, cached and
reproducible offline.

## Status

Pre-release. The converters are being extracted from a working
document-analysis pipeline (government AI-policy corpus); first public release
planned for **August 2026** as a single package with per-format extras
(`[docx]` / `[xlsx]`) — VLM ships prepared behind `[vlm]` but inactive in v1.
PDF is out of scope for this project (see
`docs/converter-viability-assessment-2026-08-04.md`).

PyPI names `refigure` and `refigure-md` are reserved (placeholder 0.0.0).

## CLI

`refigure` installs a console command — a thin wrapper over the same
`convert()` used programmatically, no separate logic:

```bash
refigure report.docx                      # markdown to stdout
refigure report.docx -o report.md         # markdown to a file
cat report.docx | refigure --format docx  # stdin, format hint required
refigure reports/ -o out/                 # batch: directory, walked recursively
refigure a.docx b.xlsx -o out/            # batch: 2+ explicit sources
```

Batch mode (2+ sources, or a single directory) requires `-o DIR`, keeps
going past a failed source by default (`--fail-fast` aborts on the first
one instead), and always prints a summary (`N/M converted, K failed`) to
stderr. `--json` emits the full result — markdown plus chart/group counts
and warnings — instead of plain markdown. `-v`/`-q` control verbosity;
`--strict` is forwarded to the same `Config.strict` the Python API uses.

Exit codes:

| Code | Meaning |
| --- | --- |
| 0 | success |
| 1 | batch mode: 1+ sources failed (keep-going default) |
| 2 | usage error (bad arguments/flags) |
| 3 | input isn't a valid document of its format |
| 4 | input isn't a valid/safe archive |
| 5 | the format's extra (`[docx]`/`[xlsx]`) isn't installed |
| 6 | unexpected internal error |

## License

Apache-2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).

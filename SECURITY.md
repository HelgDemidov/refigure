# Security Policy

## Supported versions

refigure is pre-1.0 and moves fast (see the versioning note in
[pyproject.toml](pyproject.toml)/the project's own README). Only the most
recently published version on PyPI is supported — there is no backport
policy for older releases.

## Reporting a vulnerability

Please report security issues privately — not through a public GitHub
issue.

- Preferred: [GitHub private vulnerability reporting](https://github.com/HelgDemidov/refigure/security/advisories/new)
  ("Report a vulnerability" under this repo's Security tab).
- Alternative: email helg.demidov88@gmail.com.

Include, if you can: the affected version, a minimal reproduction (an
input file or config that triggers the issue), and the potential impact.

This is a single-maintainer, pre-1.0 open-source project — there's no
formal SLA, but reports are read and triaged personally, generally within
a few days.

## Scope

refigure parses untrusted DOCX/XLSX files (OOXML zip archives) and,
when the optional `[vlm]`/`[vlm-direct]` extras are enabled, sends
rendered figure crops to a configured VLM provider. The classes of issue
most relevant to that surface: zip-bomb/decompression-ratio abuse, XML
entity expansion, path traversal via archive member names, and
injection through CLI/MCP file-path or config handling.

The CI pipeline (`.github/workflows/ci.yml`) runs ruff's `S` (security)
ruleset, Trivy secret scanning, `pip-audit`, and CodeQL on every change —
that configuration, not this file, is the current source of truth for
what's actively checked.

# Multi-stage: builder produces a wheel from the local tree (not PyPI — no
# dependency on PyPI's own CDN propagation delay, caught live during the
# 0.3.1 release: a freshly published version stayed invisible on the
# "latest" JSON alias for a few minutes). Runtime installs from that wheel
# plus the one dependency PyPI/uvx structurally cannot deliver: the system
# soffice/LibreOffice binary the VLM composite-figure path shells out to
# (see pyproject.toml's [vlm] comment).
FROM python:3.12-slim-bookworm AS builder
WORKDIR /build
COPY . .
RUN pip install --no-cache-dir build && python -m build --wheel

FROM python:3.12-slim-bookworm

LABEL org.opencontainers.image.source="https://github.com/HelgDemidov/refigure"
LABEL org.opencontainers.image.description="DOCX/XLSX -> Markdown conversion with native OOXML chart-data extraction + optional VLM interpretation. CLI (refigure) and MCP server (refigure-mcp) in one image."
LABEL org.opencontainers.image.licenses="Apache-2.0"

# libreoffice-writer, not the full libreoffice metapackage — same apt
# package ci.yml's test-unit/test-integration jobs already install on the
# runner for the real (non-mocked) soffice-render path. --no-install-recommends
# keeps recommended-but-unneeded packages (other LibreOffice components,
# help files) out.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libreoffice-writer \
    && rm -rf /var/lib/apt/lists/*

# [full] = mcp+docx+xlsx+vlm-direct (pyproject.toml) — every tool, both
# formats, every VLM provider. Docker is the one distribution format where
# installing everything by default is the right call: it's also the format
# that alone can deliver the VLM path turnkey (via the soffice install
# above), so there's no lighter "just the CLI" image worth shipping
# separately — same reasoning that dropped the earlier two-tag idea from
# this feature's brainstorming (both console scripts already coexist
# fine in one env, proven by the [full] extras-isolation CI leg).
COPY --from=builder /build/dist/*.whl /tmp/
RUN pip install --no-cache-dir "$(ls /tmp/*.whl)[full]" && rm -rf /tmp/*.whl

# Non-root for the actual runtime — package installs above still ran as
# root, which is fine (nothing in this stage after this point needs it).
# /data exists and is writable up front: the README's own bind-mount
# examples (`-v $PWD:/data`) assume it, and a root-owned mkdir here means
# the non-root user doesn't need write access to `/` itself.
RUN useradd --create-home --uid 1000 refigure \
    && mkdir -p /data \
    && chown refigure:refigure /data
USER refigure
WORKDIR /data

# No fixed ENTRYPOINT — refigure and refigure-mcp are both already on
# PATH; the caller picks one as the first argument to `docker run` (CLI
# conversion vs MCP server, stdio or --transport http). CMD only covers
# the no-arguments case, self-documenting rather than silently doing
# nothing.
CMD ["refigure", "--help"]

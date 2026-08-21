"""soffice per-conversion profile isolation (mcp-server-phase1-skeleton
spec §1): concurrent ``soffice --headless`` invocations sharing the
default LibreOffice profile conflict on its lock file (measured live: ~1/3
failures at 3 concurrent renders, see the spec) — ``_render_via_soffice``
now requires a caller-supplied ``profile_dir``, and
``enhance_docx_markdown`` creates exactly ONE per conversion call, shared
across every group marker in it (not a fresh one per marker)."""

from __future__ import annotations

import subprocess

import pytest

from refigure import vlm
from refigure.api import Config
from refigure.vlm.cache import InMemoryCacheBackend

from ..docx.test_docx import build_minimal_docx

_GID_A = "aaaaaaaaaaaa"
_GID_B = "bbbbbbbbbbbb"


def _two_group_markdown() -> str:
    return (
        f"> [Figure, docx group {_GID_A} — composite content not analyzed]\n"
        "> captions: first caption\n"
        "\n"
        f"> [Figure, docx group {_GID_B} — composite content not analyzed]\n"
        "> captions: second caption"
    )


class _FakeRenderedImage:
    def save(self, buf: object, format: str, quality: int) -> None:
        buf.write(b"fake-jpeg-bytes")  # type: ignore[attr-defined]


class _FakeOriginal:
    def convert(self, mode: str) -> _FakeRenderedImage:
        return _FakeRenderedImage()


class _FakeToImageResult:
    original = _FakeOriginal()


class _EmptyPdfPage:
    """Minimal pdfplumber page stand-in — empty rects/curves/images/chars
    so ``_content_bbox`` degrades to ``None`` (the uncropped branch),
    enough to reach ``_render_via_soffice``'s argv, which is all this
    module's tests care about (mirrors ``test_vlm.py``'s own
    ``_FakePdfPage``, not imported cross-file to keep this module
    self-contained)."""

    def __init__(self) -> None:
        self.rects: list[dict[str, float]] = []
        self.curves: list[dict[str, float]] = []
        self.images: list[dict[str, float]] = []
        self.chars: list[dict[str, float]] = []
        self.bbox: vlm.BBox = (0, 0, 100, 100)

    def crop(self, bbox: vlm.BBox) -> "_EmptyPdfPage":
        return self

    def to_image(self, resolution: int) -> _FakeToImageResult:
        return _FakeToImageResult()


class _EmptyPdfDocument:
    def __init__(self) -> None:
        self.pages = [_EmptyPdfPage()]

    def __enter__(self) -> "_EmptyPdfDocument":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def test_render_via_soffice_argv_includes_env_user_installation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: vlm.Path
) -> None:
    captured_cmd: list[str] = []

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_cmd.extend(cmd)
        outdir_index = cmd.index("--outdir") + 1
        pdf_path = vlm.Path(cmd[outdir_index]) / "obj.pdf"
        pdf_path.write_bytes(b"%PDF-fake")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(vlm.subprocess, "run", _fake_run)
    monkeypatch.setattr(vlm.pdfplumber, "open", lambda path: _EmptyPdfDocument())

    vlm._render_via_soffice(
        b"docbytes",
        suffix=".docx",
        raw_name="doc.docx",
        obj_id="id1",
        obj_kind="group",
        profile_dir=tmp_path,
    )

    matches = [c for c in captured_cmd if c.startswith("-env:UserInstallation=file://")]
    assert matches == [f"-env:UserInstallation=file://{tmp_path}"]


def test_enhance_docx_markdown_reuses_one_profile_dir_across_group_markers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_profile_dirs: list[object] = []

    def _fake_render_group_safely(
        source: object, id12: str, *, raw_name: str, strict: bool, profile_dir: object
    ) -> str | None:
        seen_profile_dirs.append(profile_dir)
        return f"data:image/jpeg;base64,{id12}"

    monkeypatch.setattr(vlm, "_render_docx_group_safely", _fake_render_group_safely)

    class _StubClient:
        def send(self, prompt: str, image_uri: str, *, model: str) -> str:
            return "a description"

    docx_bytes = build_minimal_docx(["irrelevant text"])
    config = Config(
        use_vlm=True,
        vlm_cache=InMemoryCacheBackend(),
        vlm_client=_StubClient(),  # type: ignore[arg-type]
        # Not testing the witness gate here — the stub description's text
        # has nothing to do with the synthetic captions, which would
        # otherwise fire a real (unrelated) figure-witness-recall warning.
        vlm_witness_min_recall=0.0,
    )

    new_markdown, vlm_used, warnings = vlm.enhance_docx_markdown(
        _two_group_markdown(), docx_bytes, config=config
    )

    assert vlm_used is True
    assert len(seen_profile_dirs) == 2
    assert seen_profile_dirs[0] == seen_profile_dirs[1], (
        "each group marker got its own profile_dir instead of reusing one per conversion"
    )


def test_render_failure_while_soffice_available_appends_visible_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vlm, "_soffice_available", lambda: True)

    def _fake_render_group_safely(*args: object, **kwargs: object) -> str | None:
        return None  # soffice installed, but this specific render call failed

    monkeypatch.setattr(vlm, "_render_docx_group_safely", _fake_render_group_safely)

    docx_bytes = build_minimal_docx(["irrelevant text"])
    config = Config(use_vlm=True, vlm_cache=InMemoryCacheBackend())

    new_markdown, vlm_used, warnings = vlm.enhance_docx_markdown(
        _two_group_markdown(), docx_bytes, config=config
    )

    assert vlm_used is False
    assert warnings == [f"vlm-render-failed: {_GID_A}", f"vlm-render-failed: {_GID_B}"]


def test_soffice_missing_entirely_stays_silent_not_a_new_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard, narrower than 'any unresolved marker gets a
    warning' (see the spec's §1 scope note): soffice not installed at all
    is a separate, pre-existing, deployment-wide condition — already
    server-logged, deliberately not surfaced in the client-facing
    warnings list, unlike the concurrent-render-failure case above."""
    monkeypatch.setattr(vlm, "_soffice_available", lambda: False)

    docx_bytes = build_minimal_docx(["irrelevant text"])
    config = Config(use_vlm=True, strict=False, vlm_cache=InMemoryCacheBackend())

    new_markdown, vlm_used, warnings = vlm.enhance_docx_markdown(
        _two_group_markdown(), docx_bytes, config=config
    )

    assert vlm_used is False
    assert warnings == []

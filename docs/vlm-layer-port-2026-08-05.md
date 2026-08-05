# Спецификация: перенос VLM-слоя (стадия 4b)

**Статус:** черновик v1 · 2026-08-05
**Ветка:** `feat/vlm-layer-port`

> Существенно превышает ≤100 строк — это самая тяжёлая стадия (25% v1,
> `docs/execution-sequence-2026-08-04.md`), с реальным архитектурным
> редизайном (не line-for-line переносом), полным списком новых
> зависимостей/файлов и большим планом коммитов; сжатие ниже потеряло бы
> проверяемость конкретных решений.

## 0. Что и зачем

VLM-интерпретация составных фигур DOCX (`refigure.docx.convert()`,
`config.use_vlm=True`) — облачное описание+mermaid-диаграмма для того,
что chart-движок и `docx_groups.py` честно оставляют голым маркером
(«composite content not analyzed» / «raster content not analyzed»). За
`[vlm]` extra + runtime-тоггл, **не активна/не анонсируется в v1**
(`docs/v1-scope-and-api-design-2026-08-04.md` §1/§5) — код готов и
протестирован, релиз v1 (стадия 8) её не ждёт.

**Скоуп жёстко ограничен DOCX.** XLSX не имеет VLM-пути вообще — у
G2AI_ME `figures_vlm.py` это уже так (`xlsx-чарты БОЛЬШЕ НЕ идут через
VLM... резолюция data-driven» — прямая цитата из исходника, строки
73-75): нечитаемый нативный чарт остаётся честным static-маркером
навсегда, эскалации нет. `config.use_vlm=True`, переданный в
`xlsx.convert()`, — молчаливый no-op (общий `Config`, специального кода
в `xlsx.py` не требуется). PDF полностью вне скоупа проекта — и вне
скоупа этой стадии: подавляющая часть `figures_vlm.py` (676 строк) —
PDF-специфичный код (`_FIGURE_MARKER_RE`/`_IMAGE_MARKER_RE`,
`_find_region`/`_find_raster_image`, `pdfplumber`-кроп PDF-СТРАНИЦЫ) —
не переносится вовсе.

## 1. Что реально переносится из G2AI_ME (файл-функция-строки)

Из `pipeline/scripts/convert/figures_vlm.py` (676 строк) — только:
`_DOCX_IMAGE_MARKER_RE`/`_docx_media_uri` (standalone-изображение →
data-URI из word/media/*, без рендера), `_DOCX_GROUP_MARKER_RE`/
`_render_docx_group`/`_render_via_soffice`/`_content_bbox` (композитная
группа → мини-docx → soffice → PDF → кроп по content-bbox → JPEG),
`FIG_PROMPT`/`_build_payload`/`_call_vlm_uri`, `_demote_headings`/
`_gate_mermaid_fences`/`sanitize_vlm_markdown`, `witness_defects`,
`_render_injected_docx_image`/`_render_injected_docx_group`/
`_render_injected`. `has_bare_markers` не нужна отдельно — сканирование
уже часть `enhance_docx_markdown` (§3).

Из `core/openrouter.py` (80 строк, **уже полностью sync** — подтверждено
чтением, не предположено, см. `v1-scope-and-api-design` §3) — весь файл
переносится почти как есть: `chat_request`/`InbandError`/`RETRY_SCHEDULE`.

Из `core/markers.py` (47 строк) — `injection_open`/`injection_end`
грамматика, переносится целиком, сворачивается в `vlm.py` (слишком
маленький и VLM-специфичный для отдельного core-модуля).

Из `convert/lint.py` (177 строк) — только 3 чистые функции без внешних
зависимостей: `token_recall`/`numeric_counter`/`format_missing_side`
(нужны `witness_defects`). Остальное (`lint_conversion`/`witness_checks`/
OCR-специфика) не нужно.

**НЕ переносится:** `core/fsio.py` целиком (`atomic_write_text`/
`exclusive_flock` — файловая staging-политика персистентного пайплайна;
у refigure нет «doc.md на диске», см. §2) и весь PDF-код `figures_vlm.py`.

## 2. Архитектурный редизайн (не line-for-line порт)

G2AI_ME's `apply_figures_pass(md_path: Path, raw: Path, *, model)` —
ОТДЕЛЬНЫЙ проход поверх уже ЗАПИСАННОГО на диск `doc.md`, кэш —
`raw.parent / ".figures.yaml"`. У refigure нет ни файла на диске
(`convert()` — одна in-memory функция, вход `Path | bytes | BinaryIO`),
ни «родительской директории» у байтового входа. Редизайн (решение,
не перенос):

- Новая точка входа `vlm.enhance_docx_markdown(markdown: str, source:
  Path | bytes, *, config: Config) -> tuple[str, bool, list[str]]`
  (markdown, vlm_used, warnings) — сканирует СТРОКУ (не файл) на голые
  маркеры. `refigure/docx.py`'s `convert()` уже производит ИДЕНТИЧНУЮ
  маркер-грамматику (`> [Figure, docx group {id} — composite content not
  analyzed]` / `> [Image, docx media {id} — raster content not
  analyzed]` — проверено живым выводом CLI на реальной фикстуре) —
  ничего в docx.py не меняется для совместимости, грамматика уже та же.
- Вызывается из `docx.py`'s `convert()` В КОНЦЕ, после сборки
  `markdown = text + "\n" + fallback`, гейтед `if config.use_vlm:`.
  **Импорт `vlm.py` — ленивый**, внутри этого блока, НЕ на уровне
  модуля `docx.py` — тот же приём, что уже принят в `refigure/cli.py`
  (`_convert_fn`): `refigure[docx]`-инсталляция без `[vlm]` не должна
  падать на голом `import refigure.docx`, только при попытке реально
  использовать VLM (`MissingOptionalDependencyError`, guard в `vlm.py`).
- **Кэш — подключаемый бэкенд, не хардкод sidecar** (design-документ §3,
  прямое требование): `VlmCacheBackend` Protocol (`get(key) -> dict |
  None`, `set(key, value) -> None`). **Протокол определён в `api.py`**
  (core, уже дом `Config`/`ConversionResult`), не в `vlm_cache.py` —
  самокритика раунда 2: обратный вариант («core ссылается на тип из
  периферийного vlm-модуля») разворачивает направление слоёв, которое
  везде в проекте держится строго (core ничего не знает о per-формат/
  per-возможность модулях, только наоборот). `vlm_cache.py` импортирует
  Protocol ИЗ `api.py`, даёт конкретные реализации:
  `InMemoryCacheBackend` (дефолт, без диска, безопасно для библиотеки,
  не пайплайна) и `FileCacheBackend(path)` — удобство, не обязательно:
  **JSON, не YAML** — G2AI_ME's `.figures.yaml` был человекочитаем ради
  git-диффов корпуса, у refigure такого сценария нет, а PyYAML — новая
  зависимость, которой в проекте вообще нет нигде (`tests/integration/
  conftest.py`'s docstring это уже фиксирует для другого случая); JSON —
  stdlib, ноль новых зависимостей ради этой части.
- `docx_groups.extract_group_docx(raw: Path, id12: str)` **уже
  существует** в refigure (не новый код) — но сигнатура `Path`-only;
  расширяется до `Path | bytes` (как `normalized` везде в пакете) —
  маленькая правка уже перенесённого файла, не риск.

## 3. Новые модули и зависимости

- `refigure/vlm_client.py` — `chat_request`/`InbandError` (порт
  `core/openrouter.py`).
- `refigure/vlm_cache.py` — `InMemoryCacheBackend`, `FileCacheBackend`
  (both implement `VlmCacheBackend`, defined in `api.py` — see §2).
- `refigure/vlm.py` — маркер-грамматика (docx-only), `sanitize_vlm_markdown`
  (переиспользует уже существующий `chart_render.mermaid_renders` для
  mermaid-гейта — ноль нового кода там), `witness_defects` (применим
  ТОЛЬКО к composite-группам, не к standalone-изображениям — в
  G2AI_ME'шном исходнике `docx_image_matches`-цикл `witness_defects` не
  вызывает вовсе, `_DOCX_IMAGE_MARKER_RE` не несёт witness-группы; легко
  упустить при портировании, явно фиксируется здесь),
  `_docx_media_uri`/`_render_docx_group`/`_render_via_soffice`,
  `enhance_docx_markdown`. Guard: `try: import pdfplumber except
  ImportError: raise MissingOptionalDependencyError("refigure[vlm] is
  required...")` — тот же паттерн module-level try/except + capability-
  флаг, что `docx.py`/`xlsx.py`/`chart_render.py`, НЕ новый механизм. **Guard
  — первая строка файла**, перед `from . import docx_groups, chart_render`
  (оба сейчас безопасны — ни один не тянет pdfplumber транзитивно — но
  порядок держится по дисциплине PR #8/`project_extras_isolation_bug`
  memory: гвард после same-package импорта работает только пока их
  внутренности случайно безопасны, не по контракту).
- `pyproject.toml`: `[project.optional-dependencies] vlm = ["pdfplumber>=…"]`.
  **`pdfplumber` — не PDF-конвертация**, это утилита кропа PDF,
  сгенерированного LibreOffice как ПРОМЕЖУТОЧНЫЙ рендер мини-docx (тот
  же приём, что `_render_via_soffice` в G2AI_ME) — ни входной, ни
  выходной формат PDF не поддерживается, PDF никогда не покидает эту
  одну internal-функцию. Явно проговорено здесь, чтобы не читалось как
  тихий откат решения «PDF вне скоупа».
- `soffice` (LibreOffice) — системный бинарник, не pip-пакет, не в
  extras (не может быть). `shutil.which("soffice") is None` →
  `logger.warning` + маркер остаётся как есть (zero-loss floor,
  тот же приём, что G2AI_ME) — никогда hard-fail.
- `Config` (`api.py`) — новые поля: `use_vlm: bool = False`,
  `vlm_model: str = "google/gemini-3-flash-preview"` (дефолт G2AI_ME,
  «победитель пилота» — `cloud_ocr.DEFAULT_VLM_MODEL`),
  `vlm_api_key: str | None = None` (фоллбэк на `OPENROUTER_API_KEY` env,
  explicit-param-overrides-env — конвенция типичных SDK), `vlm_cache:
  VlmCacheBackend | None = None` (фоллбэк на `InMemoryCacheBackend()`).
- `ConversionResult.vlm_used` (уже есть в датаклассе с стадии 2, сейчас
  везде `False`) — становится реальным полем для docx-пути.

## 4. Вне скоупа этой стадии

- Async batch-точка входа — явно отложено design-документом §3 до v1.x
  («решается когда VLM реально пойдёт в разработку... не блокирует
  дизайн API v1» — и roadmap §5 закрепляет это именно за v1.x, не 4b).
  `vlm.py` остаётся 100% sync, как ядро.
- Активация/анонс `[vlm]` в README как готовой фичи — v1.x, не сейчас.
- XLSX VLM-путь — не существует, см. §0.
- OCR/scan-VLM (`cloud_ocr.py`, второй потребитель `core/openrouter.py`
  в G2AI_ME) — PDF/скан-специфика, вне проекта целиком.

## Тестовое покрытие

- `tests/unit/test_vlm_client.py` — порт `tests/unit/core/test_openrouter.py`
  (173 строки, формат-агностичный) — retry-лестница, `InbandError`,
  мокнутый `urllib.request.urlopen` (без сети).
- `tests/unit/test_vlm_cache.py` — Protocol conformance для обоих
  бэкендов, `FileCacheBackend` — реальная JSON-персистентность на
  `tmp_path`.
- `tests/unit/test_vlm.py` — селективный порт релевантного (docx-only)
  подмножества `tests/unit/convert/test_figures_vlm.py` (1079 строк в
  G2AI_ME, в основном PDF — переносится не файл целиком, а сценарии):
  маркер-сканирование, `sanitize_vlm_markdown`, `witness_defects`,
  **cache-hit-only офлайн-путь** `enhance_docx_markdown` (тот же приём,
  что G2AI_ME's golden self-check — предзаполненный `InMemoryCacheBackend`,
  ноль сети/API-ключа в тесте).
- Расширить `tests/unit/test_optional_dependency_guards.py`:
  `("refigure.vlm", "pdfplumber")` в `_POISON_CASES` (уже анонсировано в
  файле как задел); плюс — `docx.convert(source, config=Config(use_vlm=False))`
  не падает и не импортирует `vlm`/`pdfplumber` даже когда тот отравлен
  (доказывает ленивость импорта, не только наличие guard'а).
- Расширить `tests/extras/test_extras_isolation.py` + `ci.yml`'s
  `test-extras` матрицу веткой `vlm` (+ комбинации `docx+vlm`) — прямое
  требование `execution-sequence` §4b, тот же урок про порядок импортов
  (PR #8), что уже нашла матрица для `xlsx.py`.
- Порт `tests/integration/test_docx_groups_live.py` (отложен с стадии 5,
  `project_deferred_docx_groups_live_test` memory) — известные group-id
  (`31cb26ede622`, `863b94a50ac0`, `5fef7b6067d0`, `cf269e703022`,
  `33be0a31a485`, `34d4b5014cb4`) против уже имеющейся фикстуры
  `iot-report-2022-national-strategies-excerpt.docx`, `skipif` по
  `shutil.which("soffice")`.

## План коммитов/PR

1. `feat: add refigure/vlm_client.py — sync OpenRouter chat client (ported from core/openrouter.py)`
2. `feat: add VlmCacheBackend protocol to api.py + refigure/vlm_cache.py's in-memory/file backends`
3. `feat: widen docx_groups.extract_group_docx to accept Path | bytes`
4. `feat: add refigure/vlm.py — docx marker grammar, sanitize_vlm_markdown, witness_defects`
5. `feat: refigure/vlm.py — docx image/group resolution (soffice render, VLM call, injection)`
6. `feat: refigure/vlm.py — enhance_docx_markdown() public entry point`
7. `feat: extend Config with use_vlm/vlm_model/vlm_api_key/vlm_cache`
8. `feat: wire vlm.enhance_docx_markdown into docx.convert() behind lazy import + use_vlm gate`
9. `feat: pyproject.toml — [vlm] extra (pdfplumber)`
10. `test: tests/unit/test_vlm_client.py — ported retry-ladder coverage`
11. `test: tests/unit/test_vlm_cache.py — protocol conformance`
12. `test: tests/unit/test_vlm.py — marker scan/sanitize/witness/offline cache-hit path`
13. `test: extend test_optional_dependency_guards.py for refigure.vlm + lazy-import-when-use_vlm=False`
14. `test: extend tests/extras/test_extras_isolation.py + ci.yml matrix — vlm leg`
15. `test: port tests/integration/test_docx_groups_live.py (soffice-gated)`
16. `docs: note [vlm] extra exists, not promoted — README/CLAUDE.md`

## Чек-лист реализации

- [ ] `refigure/vlm_client.py`
- [ ] `refigure/vlm_cache.py`
- [ ] `docx_groups.extract_group_docx` accepts `Path | bytes`
- [ ] `refigure/vlm.py` — grammar + sanitize + witness_defects
- [ ] `refigure/vlm.py` — docx image/group resolution
- [ ] `refigure/vlm.py` — `enhance_docx_markdown()`
- [ ] `Config` extended
- [ ] `docx.convert()` wired (lazy import, `use_vlm` gate)
- [ ] `[vlm]` extra in `pyproject.toml`
- [ ] `test_vlm_client.py`
- [ ] `test_vlm_cache.py`
- [ ] `test_vlm.py`
- [ ] `test_optional_dependency_guards.py` extended
- [ ] `test_extras_isolation.py` + `ci.yml` `vlm` leg
- [ ] `test_docx_groups_live.py` ported
- [ ] README/CLAUDE.md note

# Спецификация: Извлечение конвертеров + публичный API (стадия 2)

**Превышает бюджет ≤100 строк** — стадия помечена в execution-sequence как
«точка ветвления» (15%, второй по весу после VLM), реальный API-дизайн, не
механический перенос.

**Статус:** черновик v1 · 2026-08-04
**Ветка:** `feat/stage2-public-api-wrapper`

## 0. Что и зачем

`_convert_docx`/`_convert_xlsx` в G2AI_ME (`converters.py:425-477, 540-594`)
пишут в файл (`out: Path`), принимают неиспользуемые `language`/`record`
(нужны только PDF/OCR-ветке того же реестра) и бросают голый `ConversionError`.
Цель стадии: обёртка `refigure.docx.convert()`/`refigure.xlsx.convert()`,
возвращающая `ConversionResult` (§3 design-документа) — с типизированными
исключениями, `Config`, входом `Path | bytes | BinaryIO`.

Новый код (`api.py`, `_io.py`, `docx.py`, `xlsx.py`, тесты) — **на английском
с нуля** (docstring/комментарии), без grandfather-исключения стадии 1: то
исключение было только для уже написанных G2AI_ME-файлов, ожидающих стадию 4.

## 1. Публичные типы — `refigure/api.py` (новый, core-tier: без mammoth/openpyxl)

```python
@dataclass
class Config:
    strict: bool = False  # см. §6 — в этой стадии не ветвит поведение

@dataclass
class ConversionResult:
    markdown: str
    warnings: list[str] = field(default_factory=list)
    charts_found: int = 0
    charts_rendered: int = 0
    groups_found: int = 0
    vlm_used: bool = False  # всегда False до стадии 9

class UnsupportedFormatError(Exception): ...
class CorruptArchiveError(Exception): ...
class MissingOptionalDependencyError(Exception): ...
```

**Где что бросается** (сейчас нигде явно не мапится — реальный пробел
источника, не перенос): `CorruptArchiveError` ← `zipsafe.ArchiveBombSuspected`
и `zipfile.BadZipFile` (не zip вовсе). `UnsupportedFormatError` ← `docx.py`/
`xlsx.py` ловят исключение самого `mammoth.convert_to_html`/
`openpyxl.load_workbook` (структурно не docx/xlsx — напр. `.doc` под чужим
расширением) и оборачивают, а не пропускают наружу сырым. Сегодня в G2AI_ME
это исключение никем не ловится (не библиотечный код, ловушка per-doc выше по
стеку) — для refigure оставлять утечку внутреннего исключения нельзя.
`MissingOptionalDependencyError` ← импорт `docx.py`/`xlsx.py` без extra.

## 2. Вход `Path | bytes | BinaryIO` — что реально нужно менять

Проверено (реализация; черновик спеки здесь ошибался — см. ниже):
`zipfile.ZipFile`/`openpyxl.load_workbook` принимают путь ИЛИ файлоподобный
объект (с `read`/`seek`), но **не сырые `bytes` напрямую** — это выяснилось
только при реализации, живым тестом (`zipfile.ZipFile(b"...")` ->
`AttributeError: 'bytes' object has no attribute 'seek'`). Значит, любое
место, где `raw`/`path` может прийти как `bytes`, обязано завернуть его в
`io.BytesIO` перед вызовом — таких мест 3, не 1.

- Новый `refigure/_io.py` (приватный, core-tier): `normalize_source(source:
  Path | bytes | BinaryIO) -> Path | bytes` — `Path` пропускает как есть,
  `bytes` как есть, `BinaryIO` -> `.read()`.
- `zipsafe.check_archive`: тип `Path` -> `Path | bytes`, внутри —
  `path if isinstance(path, Path) else io.BytesIO(path)` перед
  `zipfile.ZipFile(...)`; `path.name` в сообщениях об ошибке -> условно.
- `docx_groups.extract_and_strip_groups`: тип `raw: Path` -> `Path | bytes`,
  `orig = raw.read_bytes() if isinstance(raw, Path) else raw`.
- `xlsx_charts.iter_chart_entries`: тип `raw: Path` -> `Path | bytes`, тот же
  `io.BytesIO`-паттерн, что в `zipsafe.check_archive` — **этого не было в
  черновике** (ошибочно решил, что не нужно).
- `openpyxl.load_workbook` в `refigure/xlsx.py`: `bytes` заворачивается в
  `io.BytesIO` на месте вызова (сам `openpyxl` не модифицируется).

## 3. `refigure/docx.py`

```python
try:
    import mammoth
    from markdownify import ATX, MarkdownConverter
except ImportError as exc:
    raise MissingOptionalDependencyError(
        "refigure[docx] required to convert DOCX files"
    ) from exc

def convert(source: Path | bytes | BinaryIO, *, config: Config | None = None) -> ConversionResult: ...
```

Тело — перенос `_convert_docx` (`converters.py:425-477`), адаптировано:
`out.write_text(...)` -> `return ConversionResult(markdown=text + "\n" +
fallback, ...)`; `language`/`record` параметры не переносятся (не используются
телом функции — проверено чтением, не предположено).

`charts_found`/`groups_found` — из `DocxGroup.kind` (`chart`/`group`) списка,
который уже возвращает `extract_and_strip_groups`. `charts_rendered` требует
знать, у скольких `kind="chart"` групп `chart_render.render_chart(...)`
реально вернул не-`None` (не просто caption-маркер) — `inject_group_markers`
(`docx_groups.py:289-314`) сейчас это считает, но не отдаёт наружу. Меняю
сигнатуру: `inject_group_markers(...) -> tuple[str, int]` (текст,
rendered_count) — не дублирую логику подсчёта в `docx.py`.

`warnings`: если `charts_found > 0 and chart_render.mermaidx is None` —
"mermaidx not installed, chart diagrams disabled, tables only" (сейчас это
только `logger.warning`, в `ConversionResult.warnings` не попадает — тоже
нужно исправить, чтобы попадало). Пустой `text` — **не исключение** (см. §5).

## 4. `refigure/xlsx.py`

Симметрично: перенос `_convert_xlsx` (`converters.py:540-594`), тот же
паттерн `try/except ImportError -> MissingOptionalDependencyError` для
`openpyxl`. Провенанс-рендер чарта (`_render_xlsx_chart_block`,
`converters.py:521-537`) переезжает В `xlsx.py` (в G2AI_ME он живёт в
`converters.py`, не в `xlsx_charts.py`) — здесь же считаются
`charts_found`/`charts_rendered` (по тому же принципу: рендер вернул не-`None`
или нет). `groups_found` — всегда `0` (у xlsx нет composite-групп).

## 5. Пустой результат конвертации — warning, не исключение

G2AI_ME бросает `ConversionError` на пустой doc.md (`converters.py:474, 593`)
— оправдано ДЛЯ ПАЙПЛАЙНА (куратор корпуса должен заметить). Библиотеке это
не подходит: легитимно пустой/бланковый docx/xlsx — валидный, не ошибочный,
вход. Решение: `ConversionResult(markdown="", warnings=["no extractable
content"], ...)`, не raise. Отклонение от источника — намеренное, не
недосмотр.

## 6. `strict` в этой стадии — заявленный no-op

`Config.strict` существует (уже решённая часть API-поверхности, §3
design-документа) — но ветвить в стадии 2 нечего: единственная деградируемая
способность здесь — mermaid-рендер, а table-only фолбэк уже самостоятельно
ценный результат (тот же принцип, что VLM-фолбэк), не корректно принудительно
ронять её через `strict=True`. `strict` начинает реально ветвить поведение в
стадии 9 (`use_vlm` + `[vlm]` не установлен -> `MissingOptionalDependencyError`
при `strict=True`, иначе warning). Явно, не молчаливый недоделанный кусок API.

## 7. `refigure/__init__.py`

Добавить ре-экспорт `Config`, `ConversionResult`, 3 исключения. НЕ добавлять
`from . import docx, xlsx` — голый `pip install refigure` не должен трогать
mammoth/openpyxl (§2 design-документа, уже проверено в стадии 1).

## Тестовое покрытие

Синтетические smoke-тесты (не корпусные фикстуры — те ждут стадию 3):
`tests/test_docx.py`/`tests/test_xlsx.py` собирают минимальный valid
docx/xlsx программно, без внешних файлов — ноль вопросов лицензирования.
Для xlsx — `openpyxl.Workbook()` (уже зависимость). Для docx — **не**
`python-docx` (не зависимость нигде в проекте, проверено grep) — тот же
приём, что уже есть в G2AI_ME `tests/support.py::build_minimal_docx`:
zip + сырой OOXML XML руками. Не переносить `support.py` целиком (это
стадия 5, там фикстуры под конкретные тесты) — здесь достаточно одной
локальной минимальной функции в `tests/test_docx.py`.
Гоняют через `convert()`, проверяют форму `ConversionResult` (не бросает,
`markdown` непустой на непустом входе, счётчики согласованы).
`MissingOptionalDependencyError` без реального отсутствия mammoth/openpyxl
не тестируется — это стадия 6 (CI-матрица extras), не мокать здесь.

## План коммитов/PR

1. `feat: add Config/ConversionResult/exception types (refigure/api.py)`
2. `feat: add input-normalization helper, widen zipsafe.check_archive for bytes/streams`
3. `feat: widen docx_groups for bytes/streams, extend inject_group_markers with render count`
4. `feat: add refigure/docx.py public convert()`
5. `feat: add refigure/xlsx.py public convert()`
6. `feat: re-export public API types from refigure/__init__.py`
7. `test: synthetic smoke tests for docx.convert()/xlsx.convert()`

## Чек-лист реализации

- [x] `refigure/api.py`: `Config`, `ConversionResult`, 3 исключения
- [x] `refigure/_io.py`: `normalize_source`
- [x] `zipsafe.check_archive` принимает `Path | bytes`
- [x] `docx_groups.extract_and_strip_groups` принимает `Path | bytes`
- [x] `docx_groups.inject_group_markers` возвращает `(text, rendered_count)`
- [x] `refigure/docx.py`: `convert()`, `MissingOptionalDependencyError` на импорте
- [x] `refigure/xlsx.py`: `convert()`, `MissingOptionalDependencyError` на импорте
- [x] `refigure/__init__.py` ре-экспортирует публичные типы
- [x] synthetic smoke-тесты зелёные (12/12)
- [x] `ruff check`/`ruff format --check`/`mypy refigure` чисты
- [x] (вне исходного чек-листа) `xlsx_charts.iter_chart_entries` тоже
      потребовал `Path | bytes` — найдено при реализации, см. §2

## Вне скоупа

- VLM (`use_vlm`, реальное ветвление `strict`) — стадия 4b/9.
- Тесты на корпусных фикстурах — стадия 5 (ждёт лицензионную проверку, стадия 3).
- CI-матрица под extras (реальная проверка `MissingOptionalDependencyError`
  без установленного extra) — стадия 6.
- README/демо — стадия 7.
- Перевод стадии-1 файлов на английский — стадия 4, не эта.

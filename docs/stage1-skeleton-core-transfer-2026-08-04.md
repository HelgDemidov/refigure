# Спецификация: Скелет пакета + перенос ядра (стадия 1)

**Статус:** черновик v1 · 2026-08-04
**Ветка:** `feat/stage1-skeleton-core-transfer`

## 0. Что и зачем

Первая стадия фазы I (`execution-sequence-2026-08-04.md`, 10% трудоёмкости v1) —
единственная стадия без входящих зависимостей в графе (кроме параллельной
стадии 3), ничего дальше не стартует без неё. Цель: перенести 5
почти-неизменных модулей из G2AI_ME в `refigure/` как плоские
внутрипакетные файлы, поправив единственный содержательный дефект —
внутренние cross-import'ы, завязанные на исходный пакет `convert`.

Границы: перенос формы (файлы/импорты/структура), не содержания — логика
не переписывается, комментарии остаются на русском (перевод — стадия 4).

## 1. Целевая раскладка файлов

Строки — по `wc -l` на источнике (точнее округлённой оценки ~1400-1500 в
`v1-scope-and-api-design-2026-08-04.md`, которая включает и материал стадии 2):

| Источник (G2AI_ME `pipeline/scripts/convert/`) | Назначение (`refigure/`) | Строк | Зависимости после переноса |
|---|---|---|---|
| `zipsafe.py` | `zipsafe.py` | 61 | stdlib (`zipfile`, `pathlib`) — без изменений |
| `chart_data.py` | `chart_data.py` | 329 | `lxml` — без изменений |
| `xlsx_charts.py` | `xlsx_charts.py` | 245 | `lxml`, `openpyxl.utils` — без изменений (не зависит от chart_data/chart_render: только навигация по контейнеру XLSX, не парсинг chart-XML) |
| `chart_render.py` | `chart_render.py` | 255 | `lxml` (транзитивно), опц. `mermaidx`; `from convert.chart_data import ChartData` → `from .chart_data import ChartData` |
| `docx_groups.py` | `docx_groups.py` | 323 | `lxml`; `from convert import chart_render` → `from . import chart_render`, `from convert.chart_data import ChartData, parse_chart` → `from .chart_data import ChartData, parse_chart` |

Порядок переноса — по графу внутренних зависимостей (каждый шаг собирается и
импортируется независимо): zipsafe → chart_data → xlsx_charts → chart_render →
docx_groups.

Имена файлов не меняются (не `_xlsx_charts.py` и т.п.) — видимость модулей
решается вместе с публичным API в стадии 2, здесь чисто механический перенос.

## 2. Импорты — relative, не absolute

Единственная содержательная правка кода в этой стадии: 3 строки cross-import
(`chart_render.py` ×1, `docx_groups.py` ×2) с `convert.<module>` на relative
(`.`/`.<module>`), не на абсолютный `refigure.<module>` — стандартная практика
для внутрипакетных ссылок, не завязывается на имя пакета.

## 3. Что НЕ входит в эту стадию

- `_convert_docx`/`_convert_xlsx` из `converters.py` (617 строк) — стадия 2.
- Вызов `zipsafe.check_archive()` — в G2AI_ME живёт в `converters.py`/
  `figures_vlm.py`, НЕ внутри самих 5 модулей (проверено grep) — в refigure
  будет вызываться из `refigure/docx.py`/`refigure/xlsx.py` (стадия 2). Здесь
  переезжает только файл-утилита, без вызывающей стороны.
- Отображение `ArchiveBombSuspected` (наследует `RuntimeError`) на
  типизированные исключения refigure (`CorruptArchiveError` и т.п., §3
  design-документа) — решение стадии 2.
- `refigure/__init__.py` не меняется (`__version__` only) — публичные
  ре-экспорты решаются вместе с API стадии 2.
- Проверено: плоские sibling-файлы без изменений `__init__.py` не ломают
  «голый `pip install refigure`» (§2 design-документа) — Python не
  импортирует модули пакета автоматически без явного импорта в `__init__.py`.

## Тестовое покрытие

Реальное портирование тестов (~7 файлов + `tests/support.py`) — стадия 5,
зависит от этой стадии и от стадии 3 (лицензии фикстур). Здесь — только
защита от поломки самого переноса, не поведенческие тесты:

- Каждый из 5 модулей импортируется без ошибок под новыми relative-импортами
  (`python -c "import refigure.chart_data"` и т.д. для всех пяти).
- `ruff check refigure` и `mypy refigure` — чисто (конфиг уже в `pyproject.toml`).

## План коммитов/PR

1. `feat: port zipsafe.py archive-bomb guard`
2. `feat: port chart_data.py OOXML chart-XML parser`
3. `feat: port xlsx_charts.py chart container navigation`
4. `feat: port chart_render.py, fix relative import`
5. `feat: port docx_groups.py, fix relative imports`

Lint/type-check/import-smoke гейтят каждый коммит 1–5 (`/feature-workflow`
full-suite budget) — отдельным коммитом не идут.

## Чек-лист реализации

- [x] `zipsafe.py` перенесён
- [x] `chart_data.py` перенесён
- [x] `xlsx_charts.py` перенесён
- [x] `chart_render.py` перенесён, импорт поправлен
- [x] `docx_groups.py` перенесён, импорты поправлены
- [x] `ruff check refigure` чисто
- [x] `mypy refigure` чисто
- [x] все 5 модулей импортируются без ошибок

## Вне скоупа

- Публичный API (`ConversionResult`, типизированные исключения, `strict`) —
  стадия 2.
- Перевод комментариев/докстрингов на английский — стадия 4.
- Портирование тестов с фикстурами — стадия 5.
- `refigure/docx.py`/`refigure/xlsx.py` обёртки — стадия 2.
- VLM-слой (`figures_vlm.py`) — стадия 4b, не эта стадия.

## Статус выполнения

Смерджено 2026-08-04, PR #1 (`b78047a`), 6 коммитов на
`feat/stage1-skeleton-core-transfer` (`7dd6b66`…`4f4619d`). Все пункты
чек-листа выполнены: 5 модулей перенесены, `ruff check`/`mypy refigure`
чисты, импорт-смоук пройден по всем пяти вместе, mermaidx-путь проверен
живьём (не только импортом). По ходу поймано и починлено вне плана спеки:
отсутствовал `lxml.*` в mypy-оверрайдах `pyproject.toml`; 2 строки (не 1,
как предполагалось в §2) потребовали ручного реформата под ruff E501 —
детали в `feedback_porting_tooling_gaps` (project memory).

Не решено, вынесено в PR body: `ruff format --check .` красный на всех 5
файлах (стиль G2AI_ME расходится с ruff-форматтером) — какая стадия владеет
нормализацией формата, не определено (кандидаты: стадия 4 или 8).

Вне скоупа осталось как и планировалось (публичный API, перевод, тесты) —
см. секцию выше.

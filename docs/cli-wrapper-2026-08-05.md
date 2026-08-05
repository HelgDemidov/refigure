# Спецификация: CLI-обёртка (`refigure` консольная команда)

**Статус:** черновик v1 · 2026-08-05
**Ветка:** `feat/cli-wrapper`

> Превышает целевые ≤100 строк — синтез трёх независимых конкурентных
> ревью (§1) и полная таблица кодов выхода (§4) не сжимаются без потери
> проверяемости; оправдано сложностью задачи per `/tech-spec` §5.

## 0. Что и зачем

Тонкая CLI-обёртка над уже готовым `convert()`/`ConversionResult`
(стадия 6b, `docs/execution-sequence-2026-08-04.md`). Рыночный чек
2026-08-05: для категории «документ→markdown» CLI — ожидаемый минимум, не
опция (MarkItDown 139k★/Docling 64k★+/marker 38k★+ — все три поставляют
его как равноправный интерфейс).

Дизайн ниже — не копия одного конкурента, а **«тройной пробел»**: три
агента (по одному на MarkItDown/Docling/marker, деталь в §1) независимо
разобрали каждый CLI до уровня исходного кода. Комбинация, которую
предлагает эта спецификация, ни у одного из трёх не реализована целиком:
(1) unix-pipeline-first stdin/stdout, как у MarkItDown; (2) нативный
batch/directory-ввод, как у Docling/marker; (3) типизированные,
различимые коды выхода — которых нет **ни у одного** из трёх (у всех
либо всё коллапсирует в exit 1, либо исключения вообще не перехватываются
на границе CLI). Плюс дизайн сознательно закрывает 2 подтверждённых
открытых дефекта конкурентов: Docling issue #3811 (тихая перезапись
одноимённых файлов при плоской выходной директории) и общий для Docling
и marker пробел — нет сводки/сигнала об отказе при частичном провале
батча.

## 1. Конкурентный ландшафт (сжато)

Полные агентские отчёты (raw-исходники GitHub, конкретные номера issue,
2 претензии выборочно перепроверены лично через `WebFetch` против
`__main__.py`/`cli/main.py`) — в истории сессии, не дублируются здесь.
Ключевые оси:

| Ось | MarkItDown | Docling | marker |
|---|---|---|---|
| stdin/stdout piping | ✅ (единственный из трёх) | ❌ (только файлы) | ❌ (только файлы) |
| batch/directory ввод | ❌ (только 1 файл/stdin) | ✅ (рекурсивный обход) | ✅ (`marker`, 1 уровень) |
| типизированные коды выхода | ❌ (голый traceback, exit 1) | ❌ (всё → `Abort()`, exit 1) | ❌ (traceback / всегда exit 0 в batch) |
| fault isolation в batch | — (нет batch) | ✅ (продолжает по умолчанию) | ✅ (per-file try/except) |
| summary + ненулевой exit при частичном отказе | — | ❌ (не найдено) | ❌ (подтверждено: exit 0 всегда) |
| коллизии выходных имён | — | ❌ (issue #3811, открыт) | ✅ (подпапка на документ) |

Ни один из трёх не даёт всех шести «✅» одновременно — это и есть
целевая комбинация ниже.

## 2. Модуль и упаковка

- `refigure/cli.py` — парсер (`argparse`, stdlib — не click/typer:
  MarkItDown тоже на argparse; поверхность refigure на порядок уже
  marker/Docling, отдельная CLI-зависимость не оправдана) + `main()`.
- `refigure/__main__.py` — тонкий `python -m refigure`, зеркалит
  MarkItDown: `if __name__ == "__main__": main()`.
- `pyproject.toml`: `[project.scripts] refigure = "refigure.cli:main"` —
  без экстры, доступен в `bare`-установке (как у MarkItDown/Docling
  `standard`), сама argparse-машинерия не требует mammoth/openpyxl.
- **Формат-изоляция сохраняется на уровне CLI**: `cli.py` не делает
  top-level `from . import docx, xlsx` (`refigure/__init__.py`
  подтверждённо импортирует только `.api` — проверено чтением файла).
  Диспетчер по расширению делает **ленивый** импорт нужного подмодуля
  внутри функции обработки одного source. `refigure --help`/`--version`
  работает в `bare`; реальная конвертация без нужной extra падает
  типизированно (`MissingOptionalDependencyError`, exit 5), не
  `ModuleNotFoundError` при запуске интерпретатора — тот же контракт,
  что `xlsx.py`/`docx.py` уже дают на уровне библиотеки (см.
  `project_extras_isolation_bug` memory — тот же класс риска, тот же
  рецепт: guard до любого тяжёлого импорта).
- Диспетч по расширению файла (`.docx`→`refigure.docx`,
  `.xlsx`→`refigure.xlsx`, регистронезависимо); для stdin обязателен
  `--format {docx,xlsx}` (нет имени файла — неоткуда угадать; по духу
  как MarkItDown `-x/--extension`, но у нас 2 формата — один флаг, не
  тройка extension/mime/charset).

## 3. Режимы ввода/вывода

- **Один `SOURCE`-файл, без `-o`** → markdown в stdout.
- **Один `SOURCE`-файл + `-o FILE`** → markdown в файл (UTF-8).
- **`SOURCE` не передан** → чтение stdin целиком, обязателен `--format`.
- **2+ позиционных `SOURCE` и/или один из них — директория** → batch:
  директории обходятся рекурсивно, фильтр по `.docx`/`.xlsx`; **`-o DIR`
  обязателен** (без него — usage error, exit 2; печать нескольких
  markdown-документов подряд в один stdout решили не делать —
  неоднозначная семантика без явного разделителя).
  Режим (single vs. batch) определяется тем, **что передал пользователь**
  (один файл vs. директория/несколько аргументов), а не тем, сколько
  файлов резолвится по факту — предсказуемо, без сюрпризов на
  однофайловых директориях.
- Batch: пути в `-o DIR` сохраняют относительную структуру источника
  (`reports/2026/q1.docx` → `DIR/reports/2026/q1.md`) — сознательно
  закрывает Docling issue #3811 (плоская директория + `stem`-only имя →
  тихая перезапись при совпадающих basename из разных папок).
- **stdout зарезервирован исключительно под markdown** (или под `--json`,
  §5 — тоже единственный канал, не вперемешку); весь остальной вывод
  (warnings, прогресс, summary) — в stderr. Прямое исправление открытого
  класса багов MarkItDown: `_exit_with_error` печатает в stdout (PR #1575
  не смержен на момент исследования); поток вывода принудительно
  переоткрывается в UTF-8, не полагаясь на `sys.stdout.encoding` (у
  MarkItDown 3 независимых неслитых PR чинят именно это на живом коде).

## 4. Ошибки и коды выхода

| Код | Значение |
|---|---|
| 0 | успех (все source, либо единственный) |
| 1 | batch, keep-going режим: 1+ source провалились |
| 2 | usage error (аргументы/флаги — argparse-конвенция) |
| 3 | `UnsupportedFormatError` |
| 4 | `CorruptArchiveError` |
| 5 | `MissingOptionalDependencyError` |
| 6 | непредвиденное исключение (страховка — никогда не голый traceback до пользователя, в отличие от MarkItDown/marker single-file режима) |

- **single-source или `--fail-fast`**: первая ошибка = немедленный exit
  с её типизированным кодом.
- **batch по умолчанию** (без `--fail-fast`): один плохой файл не
  останавливает батч (как у Docling/marker) — но, в отличие от **обоих**,
  в конце печатается сводка в stderr (`N/M converted, K failed: [path,
  error]…`) и exit 1, если `K>0`. Закрывает пробел, подтверждённый и у
  Docling (нет summary/различимого exit), и у marker (batch всегда exit 0
  независимо от числа отказов, подтверждено чтением `convert.py`).

## 5. Остальные флаги

- `--strict` → пробрасывается в `Config(strict=...)`; сегодня no-op
  (см. `api.py` docstring) — форвард-совместимость на момент, когда VLM
  (стадия 4b) даст `strict` реальную семантику; не изобретаем второй флаг
  позже.
- `-v/--verbose` / `-q/--quiet` → уровень корневого `refigure`-логгера;
  переиспользует существующий `logger.warning`/`lru_cache` warn-once
  механизм (`chart_render.py`), не новый.
- `--json` → вместо markdown-текста печатает в stdout **весь**
  `ConversionResult` как JSON (`markdown` как поле +
  `charts_found`/`charts_rendered`/`groups_found`/`warnings`/`vlm_used`).
  Единственный выходной канал переключается целиком (не второй
  параллельный формат, в отличие от Docling `--to`) — дёшево, потому что
  `ConversionResult` уже rich по решённому архитектурному принципу
  (`v1-scope-and-api-design-2026-08-04.md` §3). Закрывает MarkItDown issue
  #2029 (нет структурированного вывода) почти бесплатно.
- `--version` → печатает `refigure.__version__`.
- `-h/--help` → argparse default.

## Тестовое покрытие

- `tests/unit/test_cli.py` (новый): single file → stdout/`-o`; stdin +
  `--format`; batch без `-o` → usage error exit 2; batch keep-going
  (частичный отказ → summary + exit 1) vs. `--fail-fast`; каждый
  типизированный exit-код (3/4/5/6) через существующие
  corrupt/unsupported/no-extra фикстуры `tests/unit`; `--json` валиден и
  парсится; `-v`/`-q` меняют объём stderr, не трогают stdout; batch
  сохраняет относительную структуру путей (анти-#3811 регрессия).
- Расширить `tests/unit/test_optional_dependency_guards.py`:
  `_POISON_CASES`-подобный тест — CLI, вызванный для формата B, работает,
  даже если зависимость формата A отравлена (доказывает ленивую
  per-формат изоляцию импорта в `cli.py`, не только «не падает при
  импорте модуля»).
- Расширить `tests/extras/test_extras_isolation.py` (CI-матрица,
  4 legs): `refigure --version`/`--help` работает во всех 4 legs, включая
  `bare`; конвертация нужного формата в `bare`/не том extra падает с
  exit 5 и типизированным сообщением.
- Интеграционный smoke-тест на **всём 27-файловом корпусе**
  `tests/integration/fixtures/{docx,xlsx}/` через batch-режим CLI (не 1-2
  файла — поправка 2026-08-05 по факту проверки: обе подпапки плоские, 27
  файлов реально на диске, без дублей basename) — output каждого файла
  байт-в-байт совпадает с прямым вызовом `convert()` на том же source
  (доказывает, что CLI — чистый pass-through, не дублирует логику), плюс
  сама сводка (`27/27 converted`) проверяется как отдельное утверждение.
  Не заменяет синтетический анти-#3811-тест (коллизия имён) и тест на
  вложенные директории — этот реальный корпус плоский, ни того ни другого
  не содержит.

## План коммитов/PR

1. `feat: add refigure/cli.py — argparse parser, single-file stdin/stdout/-o, typed exit codes`
2. `feat: add batch mode — directory walk, keep-going + summary, --fail-fast`
3. `feat: register refigure console_script + __main__.py`
4. `feat: add --json/--strict/-v/-q/--version flags`
5. `test: tests/unit/test_cli.py — single-file/stdin/batch/exit-codes/flags`
6. `test: extend test_optional_dependency_guards.py for CLI lazy-import isolation`
7. `test: extend tests/extras/test_extras_isolation.py — CLI legs`
8. `test: integration smoke test — full 27-fixture corpus via CLI batch mode, byte-for-byte vs convert()`
9. `docs: README CLI usage section + exit-code table`

## Чек-лист реализации

- [ ] `refigure/cli.py` — парсер, single-file режим, типизированные коды
- [ ] batch-режим (directory walk, keep-going/summary, `--fail-fast`)
- [ ] `refigure/__main__.py` + `[project.scripts]` в `pyproject.toml`
- [ ] `--json`/`--strict`/`-v`/`-q`/`--version`
- [ ] `tests/unit/test_cli.py`
- [ ] `test_optional_dependency_guards.py` расширен
- [ ] `tests/extras/test_extras_isolation.py` расширен
- [ ] интеграционный smoke-тест (весь корпус, batch-режим, byte-for-byte vs. `convert()`)
- [ ] README CLI-раздел

## Вне скоупа

- Множественные форматы вывода за один вызов (Docling `--to md --to
  json --to chunks`) — у нас один выходной канал (`--json` **заменяет**
  markdown, не дополняет).
- Glob-паттерны сверх фильтра по расширению при обходе директории.
- Плагин-система (MarkItDown `entry_points`) — не запланирована
  архитектурно, нет второго конвертера, который стоило бы подключать.
- GUI/HTTP-сервер (`marker_gui`/`marker_server`, `docling-serve`) —
  отдельная поверхность, не часть CLI-обёртки; MCP — уже v2 (стадия 10).
- Multi-node/worker-пул шардинг (`marker --num_chunks`) — неприменимо:
  refigure не делает ML-инференс, конвертация одного файла — sub-second,
  простой последовательный цикл по source в batch-режиме достаточен.

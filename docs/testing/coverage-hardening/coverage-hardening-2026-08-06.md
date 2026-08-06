# Спецификация: усиление тестового покрытия + coverage-бейдж (стадия 7b, часть 1)

> Превышает целевые ≤100 строк — §2 построчно перечисляет каждую измеренную
> дыру по каждому файлу (пользователь явно запросил закрытие реальных дыр,
> не абстрактный порог), сжатие потеряло бы именно эту конкретику.

**Статус:** черновик v1 · 2026-08-06
**Ветка:** `test/coverage-hardening`

## 0. Что и зачем

Первая из двух частей стадии 7b (`docs/project-meta/execution-sequence/
execution-sequence-2026-08-04.md`, п. 7b) — вторая (security-инструменты +
аудит) отдельный спек позже, здесь не затрагивается. Сегодня coverage
измеряется (`test-unit` джоб, PR #7) но не гейтит ничего, бейджа нет.

Замер вживую на момент постановки (не по памяти): `tests/unit` в
одиночку — 88%; `tests/unit`+`tests/integration` вместе — **94%** (1539
стейтментов, 92 непокрытых, 344 passed/3 skip, 613s). Порог гейта — **95%**
по объединённому прогону (обоснование §3), бейдж — self-hosted, не
Codecov/Coveralls-аккаунт (§4). Не «написать тестов вообще», а закрыть
конкретные уже найденные дыры (§2) + сама механика гейта/бейджа (§1, §4).

## 1. Combined-coverage измерение в CI

`test-unit`/`test-integration` (`.github/workflows/ci.yml`) — раздельные
джобы, `--cov` сегодня только в первом. План: каждый джоб пишет свои
данные в отдельный файл (`COVERAGE_FILE=.coverage.unit` /
`.coverage.integration` env var — `coverage combine` уже умеет
автообнаруживать файлы `.coverage.*`), загружает `actions/
upload-artifact@v4`. Новый джоб `coverage` (`needs: [test-unit,
test-integration]`): скачивает оба артефакта, `coverage combine`,
`coverage report --fail-under=95 -m` (сам гейт), `coverage json -o
coverage.json`. `pyproject.toml`: добавить `[tool.coverage.run]
relative_files = true` — иначе `combine` требует `[paths]`-ремаппинг между
джобами (оба чекаутятся в одинаковый относительный путь на
`ubuntu-latest`, но `relative_files` — рекомендованная практика
coverage.py для кросс-джобового combine независимо от этого совпадения).

## 2. Закрытие измеренных дыр (по файлу)

- **`refigure/vlm/__init__.py`** (84%→~98%+, крупнейшая дыра, 48 строк).
  Не live-сетевые ветки (изначальное предположение в постановке задачи не
  подтвердилось при живой проверке кода) — все закрываемы существующими
  фейками (`_ScriptedVlmClient`/`_RaisingVlmClient`,
  `tests/unit/vlm/test_vlm.py`) и моками `subprocess`/`pdfplumber`, модуль
  специально спроектирован под pluggable `VlmClient`/`VlmCacheBackend`
  Protocol именно для этого. `_docx_media_uri` (432-457): не-растровый
  формат (svg/wmf) → `None`+warning, marker_id не найден на
  re-detection → `None`+warning. `_content_bbox` (486, 491): пустая
  страница (нет rects/curves/images/chars) → `None`, вырожденный bbox
  (x0>=x1 или top>=bottom) → `None`. `_render_via_soffice` (523-556):
  `subprocess.TimeoutExpired` (мок), ненулевой `returncode` (мок), PDF
  открылся но `pdfplumber.open`/рендер упал (мок `pdfplumber.open` →
  raise). `_render_docx_group` (575-581): soffice есть, но группа не
  найдена на re-detection (мок `docx_groups.extract_group_docx` → `None`,
  отдельно от уже протестированного «soffice отсутствует»). `_call_client`
  (629-631): исключение клиента — переиспользовать `_RaisingVlmClient`
  напрямую против `_call_client`, не только через `enhance_docx_markdown`.
  `_resolve_api_key` (634-641): `Config.vlm_api_key` не задан и
  `OPENROUTER_API_KEY` не в env → `RuntimeError`. `enhance_docx_markdown`
  (712-716, 739, 749, 785, 791-796): archive-recheck упал
  (`zipsafe.ArchiveBombSuspected`/`BadZipFile`) → warning+skip;
  cache-miss `data_uri`/`text` = `None` continue-ветки для image- И
  group-маркеров по отдельности (image-сторона частично покрыта, group-
  сторона partial-cache-hit — нет).
- **`refigure/__main__.py`** (0%, 5 строк). `if __name__ ==
  "__main__":` — механический диспатч, `cli.main()` уже тестируется
  напрямую. `# pragma: no cover` на сам guard + **новый** subprocess-тест
  (`python -m refigure --help`, exit 0) — реальная ценность за пределами
  числа: сегодня НИЧТО не вызывает настоящий `-m`-entrypoint, только
  `cli.main()` in-process.
- **`refigure/cli.py`** (96%→~99%). `_exit_code_for` (75): нетипизированное
  исключение → `EXIT_INTERNAL_ERROR` fallback. `_run_single`/аналог
  (212-213): `_convert_one` вернул `result is None` через single-file
  путь (сегодня это проверено только в batch-режиме). `_resolve_batch_
  sources` (249, 252): нераспознанное расширение → `parser.error`; не
  файл и не директория → `parser.error`. `_plan_batch` (280): дедуп
  повторного `resolved` пути (тот же файл передан дважды). `_run_batch`
  (308): пустой план → `parser.error`. `_run_batch` (335): печать
  warnings по файлу в batch-режиме без `--quiet` — сегодня не проверено.
- **`refigure/docx_groups.py`** (97%→~99%). 153/156: `rId` не в
  `rel_targets` / разрешённая часть не в `names()` архива — оба
  defensive-пути на битую/отсутствующую цель диаграммы. 167:
  `chart_root is None` → пустой tuple подписей. 248: `w:body` отсутствует
  в дереве (битый docx) → без групп. 313: `target is None` → `None`. 369:
  `by_id.get(id)` не находит группу («practically impossible», но код
  есть — симулировать id12 в тексте, не совпадающий ни с одной
  найденной группой).
- **`refigure/xlsx/charts.py`** (94%→~99%). 192: `sheet_part not in
  names()` → skip. 253-258: разбор defined-name диапазонов — нет `!` →
  skip; `'Sheet Name'` в кавычках, включая экранированное `''`→`'`;
  пустое имя листа после strip → skip append. Нужна синтетическая xlsx-
  фикстура с именем листа в кавычках/апострофом.
- **`refigure/core/chart_data.py`** (98%→~100%). 68/82: два отдельных
  `if text is None: return None` в числовом парсинге (`c:pt` без
  `c:v`-потомка). 305: `ser_el.getparent() is None` — сконструировать
  элемент напрямую, без родителя, тестировать хелпер точечно, не через
  полное дерево чарта. 337: `chart_el is None` — `c:chartSpace` без
  вложенного `c:chart` → `_EMPTY`.
- **`refigure/xlsx/__init__.py`** (95%→~99%, кроме guard-строк 38-41 —
  bucket ниже). 74: `value.is_integer()` для `float`-ячейки (напр. `3.0`)
  — нужна точечная фикстура. 177: mermaidx-недоступен warning — см.
  ниже, xlsx-сторона того же паттерна что и docx.
- **`refigure/docx/__init__.py`** (95%→~99%, кроме guard-строк 36-37 —
  bucket ниже). 168: `charts_found and not chart_render.
  mermaidx_available()` — тот же приём, что уже установлен в
  `tests/unit/core/test_chart_render_missing_mermaidx.py`
  (monkeypatch `chart_render.mermaidx = None`, не изобретать новый
  механизм), проверить точный текст warning + что таблица всё равно
  рендерится. 193-198: **`Config(use_vlm=True)`-путь внутри самого
  `docx.convert()`** — сегодня НИЧТО не проверяет это сквозное
  соединение (только `enhance_docx_markdown` изолированно в
  `test_vlm.py`); новый тест — `convert(docx_bytes, config=Config(
  use_vlm=True, vlm_client=_ScriptedVlmClient(...)))`, проверить
  `result.vlm_used`/подстановку текста. Закрывает реальный функциональный
  пробел, не только цифру.
- **`refigure/core/zipsafe.py`** (94%→~100%). 75: суммарный
  распакованный размер превышает `max_total` (в отличие от уже
  протестированной ветки «один элемент больше `max_member`», см.
  `test_docx_convert_rejects_oversized_member`/`test_xlsx_convert_
  rejects_oversized_member` в `tests/unit/test_robustness.py`) — архив со
  множеством мелких элементов, сумма которых превышает порог, тот же
  файл, симметричные `test_{docx,xlsx}_convert_rejects_oversized_total`.
- **Guard-ветки, отдельный bucket, НЕ закрываются обычным unit-тестом**:
  `docx/__init__.py` 36-37 (mammoth/markdownify), `xlsx/__init__.py`
  38-41 (openpyxl), `core/chart_render.py` 40-41 (mermaidx), `vlm/
  __init__.py` 55-56 (pdfplumber) — module-level `try/except ImportError`
  guards из optional-dependency-паттерна (`CLAUDE.md`). В dev/CI-окружении
  зависимость всегда установлена, except-ветка структурно не может
  выполниться in-process. Она уже реально проверяется двумя механизмами:
  `tests/unit/test_optional_dependency_guards.py` (sys.modules poisoning
  в subprocess) и `tests/extras/test_extras_isolation.py` (7-leg CI-
  матрица, настоящие изолированные venv) — ни один не инструментирован
  coverage.py по умолчанию (subprocess-границы). Решение: явный `#
  pragma: no cover` на каждой guard-ветке с комментарием-ссылкой на оба
  механизма, не попытка поднять `COVERAGE_PROCESS_START`
  cross-process-трекинг ради ~10 строк в 4 файлах — то же самое
  соотношение цена/честность, что уже применено к `if TYPE_CHECKING:` в
  `[tool.coverage.report] exclude_also`.

## 3. Порог — 95%, не 100%

Живой combined baseline — 94%, т.е. 95% в одном шаге от сегодняшнего
состояния после закрытия §2, не искусственно завышенная планка. 100%
отвергнут: часть кода легитимно недостижима без фиктивных тестов или
разрастания `# pragma: no cover` до потери сигнала (сетевые live-VLM/
Bedrock/Vertex/Foundry интеграционные тесты уже помечены `skipif` по
дизайну — `tests/integration/test_anthropic_{bedrock,vertex,foundry}_
live.py`, 3 skip в замере). У конкурентов (MarkItDown/Docling/marker)
формально заявленного порога не нашлось — они показывают факт без
политики-минимума, внешнего ориентира «конкуренты требуют X%» не
существует.

## 4. Coverage-бейдж

Не Codecov/Coveralls-аккаунт — лишний внешний сервис+токен, проект
последовательно избегает необязательных third-party зависимостей (отказ
от LiteLLM/aisuite, OIDC вместо PyPI-токена в релиз-гейт спеке). Вместо
этого: `coverage`-джоб (§1) транслирует `coverage.json`'s
`totals.percent_covered` в shields.io endpoint-схему
(`{schemaVersion,label,message,color}`), пишет `docs/assets/
coverage-badge.json`, коммитит на `push` в `main` (не на PR — не
засорять фиче-ветки) штатным `GITHUB_TOKEN` (`permissions: contents:
write` только на этот джоб, тот же принцип минимизации blast radius, что
уже применён к `id-token: write` в релиз-гейт спеке для `publish.yml`).
README: `https://img.shields.io/endpoint?url=https://raw.
githubusercontent.com/HelgDemidov/refigure/main/docs/assets/
coverage-badge.json`. **Оговорка**: репозиторий сейчас приватный —
`raw.githubusercontent.com` не отдаёт анонимному fetcher'у shields.io
содержимое приватного репо, бейдж физически не отрендерится до флипа
репо→публичный (то же открытое решение, что уже в `docs/release/
release-gate/release-gate-2026-08-06.md` §4). Механика/тесты/гейт
реализуются сейчас независимо от этого — рендер станет живым
автоматически в момент публикации репо, без доп. кода.

## Пересчёт трудоёмкости (п. «д» постановки)

Roadmap держит ~6% предварительно для coverage-половины 7b. Живая
проверка (92 непокрытых стейтмента, из них ~80 закрываются точечными
тестами по уже существующим паттернам/фейкам, ~12 — pragma-исключение с
комментарием, плюс новый CI-джоб/combine-механика/badge-скрипт) —
оценка подтверждается, не меняется. `docs/project-meta/execution-
sequence/execution-sequence-2026-08-04.md` не требует правки.

## Тестовое покрытие

- `tests/unit/vlm/test_vlm.py` — дополнить: `_docx_media_uri` non-raster/
  not-found, `_content_bbox` degenerate, `_render_via_soffice` timeout/
  failure/pdfplumber-exception, `_render_docx_group` group-not-found,
  `_call_client` exception, `_resolve_api_key` missing-key,
  `enhance_docx_markdown` archive-recheck-failure + group-side
  partial-cache-hit-unavailable.
- `tests/unit/test_cli.py` — дополнить: `_exit_code_for` fallback,
  single-file `result is None`, `_resolve_batch_sources` оба
  `parser.error`, `_plan_batch` дедуп, `_run_batch` пустой план +
  warnings-печать; новый subprocess-тест `python -m refigure --help`.
- `tests/unit/test_docx_groups.py` — дополнить 5 defensive-веток (§2).
- `tests/unit/xlsx/test_xlsx_charts.py` — дополнить defined-name-парсинг
  (кавычки/апостроф/пустое имя), новая синтетическая xlsx-фикстура.
- `tests/unit/core/test_chart_data.py` — дополнить None-guards,
  detached-series, missing `c:chart`.
- `tests/unit/test_robustness.py` — дополнить
  `test_{docx,xlsx}_convert_rejects_oversized_total` (total-size-exceeded,
  симметрично уже существующим `..._rejects_oversized_member`).
- `tests/unit/docx/test_docx.py` — новый `Config(use_vlm=True)` сквозной
  тест + mermaidx-unavailable warning (тот же `chart_render.mermaidx =
  None` приём, что в `test_chart_render_missing_mermaidx.py`);
  `tests/unit/xlsx/test_xlsx.py` — mermaidx-unavailable warning,
  xlsx-сторона.
- `tests/unit/test_optional_dependency_guards.py` — комментарий,
  указывающий на новые `# pragma: no cover` в 4 guard-файлах (не новый
  тест — тест уже существует).

## План коммитов/PR

1. `docs: draft spec for coverage-hardening` (этот коммит, `/tech-spec`)
2. `test: close refigure/vlm/__init__.py coverage gaps`
3. `test: close refigure/cli.py branch gaps + python -m refigure smoke test`
4. `test: close refigure/docx_groups.py + refigure/xlsx/charts.py branch gaps`
5. `test: close refigure/core/chart_data.py + refigure/core/zipsafe.py branch gaps`
6. `test: docx.convert(use_vlm=True) end-to-end wiring test + mermaidx-unavailable warnings`
7. `chore: pragma-exclude the 4 optional-dependency import guards`
8. `ci: combined coverage measurement across test-unit/test-integration + 95% gate`
9. `ci: generate + commit coverage badge JSON on push to main`
10. `docs: README coverage badge`

## Чек-лист реализации

- [x] `refigure/vlm/__init__.py` дыры закрыты
- [x] `refigure/cli.py` дыры закрыты + `-m` smoke test
- [x] `refigure/docx_groups.py` + `refigure/xlsx/charts.py` дыры закрыты
- [ ] `refigure/core/chart_data.py` + `refigure/core/zipsafe.py` дыры закрыты
- [ ] `use_vlm=True` сквозной тест + mermaidx-unavailable warnings (docx+xlsx)
- [ ] 4 guard-ветки pragma-исключены с комментарием
- [ ] combined-coverage CI-джоб + `--fail-under=95`
- [ ] coverage-badge.json генерация+коммит на push в main
- [ ] README-бейдж добавлен

## Вне скоупа

- Security-инструменты/аудит — вторая часть 7b, отдельный спек.
- Codecov/Coveralls-аккаунт — сознательно отвергнуто, см. §4.
- Cross-process coverage.py-трекинг (`COVERAGE_PROCESS_START`) для
  guard-веток — отвергнуто в пользу pragma+существующих тестов, см. §2.
- Порог выше 95% — не сейчас; пересмотр после того, как корпус/сьют
  вырастут дальше, не в рамках этого спека.
- Реальный публичный рендеринг бейджа — зависит от флипа репо→публичный
  (открытое решение релиз-гейт спека), не решается здесь.

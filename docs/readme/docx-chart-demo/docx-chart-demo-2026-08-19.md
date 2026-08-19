# Спецификация: happy-path демо для DOCX native-chart→mermaid в README

**Статус:** черновик v1 · 2026-08-19
**Ветка:** `docs/docx-chart-demo`

## 0. Что и зачем

README's Demo-секция (`README.md:17-35`) сейчас показывает 2 кейса: xlsx
happy path (native chart→mermaid, `docs/assets/demo-{light,dark}.svg`) и
docx fallback (composite-figure zero-loss marker,
`docs/assets/demo-groups-{light,dark}.svg`). DOCX happy path (native
chart→mermaid) отсутствует как hero-графика — хотя технически уже работает
(`refigure/docx/__init__.py` делит `refigure/core/chart_render.py` с xlsx,
верифицировано чтением кода) и уже частично показан текстово в «Real
examples» (`examples/hackair-native-charts.md`). Задача: третий hero-SVG
блок в Demo, по паттерну двух существующих. Вне скоупа: изменения в самом
конвертере/`chart_render.py` — только подбор примера + новый ассет +
README.

## 1. Поиск лучшего примера (обязательный этап, эмпирический)

`chart_render.py` поддерживает 3 разных mermaid-типа — `pie`,
`xychart-beta` (bar/line/area/combo/scatter), `radar-beta` — не только
xychart. Это значит "сложность" реально измерима как разнообразие типов, а
не только count. `manifest.yaml` даёт per-fixture счётчики native chart
parts/numCache/strCache из ручной XML-инспекции 2026-08-04, но НЕ типы —
типы известны только после реального прогона.

Кандидаты (docx):
- `onehealth-ejp-d3.20.docx` — 5 chart parts + 1 group. Уже источник
  `demo-groups-*`; пользователь явно разрешил повторное использование, если
  выиграет по факту (не запрещено правилом "обязательно другой файл").
- `ukri-user-behaviour-survey.docx` — 44 chart parts (максимум в корпусе),
  0 groups.
- `hackair-d7.7-pilot-evaluation.docx` — 8 chart parts. Уже есть реальный
  committed output: `examples/hackair-native-charts.md` показывает 6/8
  рендерятся, все `xychart-beta` — 0 разнообразия типов на сегодня.
- `swd2021-396-platform-work-ia.docx` — 8 chart parts (78 numCache + 54
  strCache).

Исключить `swd2018-254-marine-litter-ia-annex.docx` (1 chart part, уже в
`examples/`) — уже проверено: `examples/swd2018-combo.md` содержит 0
` ```mermaid ` блоков, заведомо не happy-path.

Прогнать `refigure.docx.convert()` на каждом кандидате напрямую в Python
(REPL/scratch-скрипт, не через `gen_demo_asset*.py` — те пишутся ПОСЛЕ
выбора победителя). Зафиксировать per-file: charts_found/charts_rendered,
множество встретившихся mermaid chart_type, число серий/категорий на самый
богатый отдельный чарт, сохранность caption/title в выводе. Критерий
победителя — не сырой count, а фактическая структурная сложность лучшего
ЕДИНИЧНОГО чарта (разнообразие типов, число серий, содержательные подписи)
— по прямому указанию пользователя 2026-08-19, не по счётчикам из
manifest.yaml.

## 2. Новый demo-ассет

Новый скрипт `scripts/gen_demo_asset_docx_chart.py`, по образцу
`gen_demo_asset_groups.py` (docx: `soffice --convert-to pdf` + `pdftoppm`
для INPUT-скриншота, номер страницы находится через `pdftotext`
full-text-поиск по реальному тексту, не угадывается) и `gen_demo_asset.py`
(raw+rendered OUTPUT, rank-fade через `_mix_hex`/`ranked_fade`, тот же
шрифтовой набор `FONT_MONO`/`FONT_SANS` и палитра `THEMES`). Пишет
`docs/assets/demo-docx-chart-{light,dark}.svg`. OUTPUT — реальный(е)
` ```mermaid ` блок(и) из `docx_convert(FIXTURE)`, не хардкод — `main()`
должен содержать те же live-assertions на реальный вывод, что оба
существующих скрипта (drift ловится тестом/ассертом, не тихо устаревает).
Скрипт документирует в своём докстринге, каких кандидатов сравнили и
почему выбран победитель — тот же формат, что и «Revision 2026-08-06
(source swap)» в `gen_demo_asset_groups.py`.

## 3. README.md

Новый `<picture>`-блок в секции Demo (`README.md:17-35`), между xlsx happy
path и docx fallback — оба happy path идут перед граничным (fallback)
случаем. alt-текст по образцу двух существующих: описывает файл+механизм,
не маркетинговый.

## 4. examples/ + regression

Если победитель — НЕ `hackair-d7.7-pilot-evaluation.docx` (у которого уже
есть `examples/hackair-native-charts.md` + строка в README «Real examples»
+ запись в `tests/integration/test_readme_examples.py._EXAMPLES`):
добавить 4-ю запись тем же паттерном (committed real `convert()` output с
attribution-хедером, новая строка в `_EXAMPLES`, новая строка в README
«Real examples» таблице). Если победитель — hackair: переиспользовать
существующую запись, новых файлов в `examples/` не создавать.

`tests/integration/test_corpus_totals.py` изменений не требует —
агрегаты (`_EXPECTED_CHARTS_FOUND`=407 и т.д.) считаются по существующим
per-fixture таблицам `test_docx_corpus.py`/`test_xlsx_corpus.py`, не
зависят от того, какая фикстура попала в демо (верифицировано чтением
кода). Прогнать как есть, подтвердить 0 diff — не редактировать файл.

## Тестовое покрытие

- Если добавляется новая `examples/` запись: новый parametrize-кейс в
  `tests/integration/test_readme_examples.py._EXAMPLES` (drift guard, тот
  же механизм что у 3 существующих строк).
- `tests/integration/test_corpus_totals.py` — прогнать, подтвердить 0
  diff (не редактировать).
- Ручная проверка (не автоматизируется, как у остальных 2 demo-ассетов):
  light/dark SVG рендерятся корректно на GitHub.

## План коммитов/PR

1. feat: add scripts/gen_demo_asset_docx_chart.py — включает эмпирическое
   сравнение кандидатов (§1), решение документируется в докстринге
   скрипта; генерирует docs/assets/demo-docx-chart-{light,dark}.svg
2. docs: add docx native-chart happy-path block to README.md Demo section
3. docs: add examples/<winner>.md + test_readme_examples.py entry + README
   Real examples row (только если победитель — не hackair)
4. docs: sync CLAUDE.md status entry for this stage

## Чек-лист реализации

- [x] Прогнать 4 кандидата через `refigure.docx.convert()`, зафиксировать
      charts_found/rendered + встретившиеся mermaid-типы + сложность
      лучшего единичного чарта
- [x] Выбрать победителя, задокументировать обоснование
- [x] Написать `gen_demo_asset_docx_chart.py`, сгенерировать SVG
      (light+dark), с live-assertions на реальный `convert()`-вывод
- [x] Обновить README.md Demo-секцию (новый `<picture>`-блок между
      xlsx happy path и docx fallback)
- [x] Если победитель не hackair: добавить `examples/` запись + тест +
      строку в README «Real examples»
- [x] Прогнать `test_corpus_totals.py` и `test_readme_examples.py` —
      подтвердить зелёные
- [x] Ручная QA: light/dark рендер на GitHub (проверено через реальный
      браузер, Chrome DevTools MCP — оба SVG, обе темы, живые
      скриншоты, не LibreOffice-конвертация)
- [ ] Обновить CLAUDE.md статус (Memory/doc update convention — заменить,
      не дописывать)

## Вне скоупа

- Изменения в `refigure/core/chart_render.py`/`chart_data.py` или
  `refigure/docx/__init__.py` — вся нужная функциональность уже работает.
- Пересчёт/расширение фикстур-корпуса — поиск идёт только по уже
  committed docx-фикстурам.

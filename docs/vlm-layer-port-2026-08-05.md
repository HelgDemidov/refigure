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
исходного пайплайна `figures_vlm.py` это уже так (`xlsx-чарты БОЛЬШЕ НЕ идут через
VLM... резолюция data-driven» — прямая цитата из исходника, строки
73-75): нечитаемый нативный чарт остаётся честным static-маркером
навсегда, эскалации нет. `config.use_vlm=True`, переданный в
`xlsx.convert()`, — молчаливый no-op (общий `Config`, специального кода
в `xlsx.py` не требуется). PDF полностью вне скоупа проекта — и вне
скоупа этой стадии: подавляющая часть `figures_vlm.py` (676 строк) —
PDF-специфичный код (`_FIGURE_MARKER_RE`/`_IMAGE_MARKER_RE`,
`_find_region`/`_find_raster_image`, `pdfplumber`-кроп PDF-СТРАНИЦЫ) —
не переносится вовсе.

## 1. Что реально переносится из исходного пайплайна (файл-функция-строки)

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

У исходного пайплайна `apply_figures_pass(md_path: Path, raw: Path, *, model)` —
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
  **JSON, не YAML** — у исходного пайплайна `.figures.yaml` был человекочитаем ради
  git-диффов корпуса, у refigure такого сценария нет, а PyYAML — новая
  зависимость, которой в проекте вообще нет нигде (`tests/integration/
  conftest.py`'s docstring это уже фиксирует для другого случая); JSON —
  stdlib, ноль новых зависимостей ради этой части.
- `docx_groups.extract_group_docx(raw: Path, id12: str)` **уже
  существует** в refigure (не новый код) — но сигнатура `Path`-only;
  расширяется до `Path | bytes` (как `normalized` везде в пакете) —
  маленькая правка уже перенесённого файла, не риск.
- **HTTP-клиент к VLM — тоже подключаемый Protocol, не хардкод под
  OpenRouter** (решение 2026-08-05, по вопросу пользователя): та же
  логика, что уже применена к кэшу — «библиотеке нужна гибкость под
  чужой деплоймент» (design-документ §3) в равной мере относится и к
  провайдеру VLM-вызова, не только к persistence. `VlmClient` Protocol
  — `send(prompt: str, image_uri: str, *, model: str) -> str` —
  определён в `api.py` рядом с `VlmCacheBackend` (тот же layering-довод:
  core не должен знать про периферийные модули, только наоборот).
  `OpenRouterClient` (в `vlm_client.py`) — единственная поставляемая
  реализация, оборачивает `chat_request`/`InbandError` (порт
  `core/openrouter.py`), держит `api_key` как поле экземпляра (не
  параметр `send()` — реализационная деталь, не часть контракта
  Protocol'а).

  **Явный архитектурный принцип (решение 2026-08-05): refigure — LLM
  provider-agnostic, не привязан к OpenRouter структурно.**
  `OpenRouterClient` — дефолтная, не единственно возможная реализация.
  Прямое следствие — конфиденциальность решается не только тумблером
  `use_vlm` (opt-in/opt-out), но и ВЫБОРОМ реализации: для
  чувствительных документов можно подключить `LocalVlmClient`
  (Ollama/vLLM/любой локальный multimodal-рантайм) вместо облачного —
  данные тогда физически не покидают машину, это сильнее и честнее,
  чем полагаться на то, что «opt-in сам по себе достаточная защита».
  Локальный клиент не входит в скоуп этой стадии (никто не просил
  конкретную реализацию) — но контракт (`VlmClient` Protocol) с
  первого дня рассчитан на то, что кто-то его напишет, не требует
  правок в `vlm.py`/`docx.py` для этого.

## 3. Новые модули и зависимости

- `refigure/vlm_client.py` — `OpenRouterClient` (implements `VlmClient`
  Protocol from `api.py`, see §2), wrapping `chat_request`/`InbandError`
  (порт `core/openrouter.py`).
- `refigure/vlm_cache.py` — `InMemoryCacheBackend`, `FileCacheBackend`
  (both implement `VlmCacheBackend`, defined in `api.py` — see §2).
- `refigure/vlm.py` — маркер-грамматика (docx-only), `sanitize_vlm_markdown`
  (переиспользует уже существующий `chart_render.mermaid_renders` для
  mermaid-гейта — ноль нового кода там), `witness_defects` (применим
  ТОЛЬКО к composite-группам, не к standalone-изображениям — в
  исходном пайплайне `docx_image_matches`-цикл `witness_defects` не
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
  же приём, что `_render_via_soffice` в исходном пайплайне) — ни входной, ни
  выходной формат PDF не поддерживается, PDF никогда не покидает эту
  одну internal-функцию. Явно проговорено здесь, чтобы не читалось как
  тихий откат решения «PDF вне скоупа».
- `soffice` (LibreOffice) — системный бинарник, не pip-пакет, не в
  extras (не может быть). `shutil.which("soffice") is None` →
  `logger.warning` + маркер остаётся как есть (zero-loss floor,
  тот же приём, что у исходного пайплайна) — никогда hard-fail в самом коде.
  **Решение по CI (пересмотрено 2026-08-05 — untested CI path
  неприемлем):** `libreoffice-writer` **устанавливается в CI**
  (`test-unit` job, `apt-get install -y libreoffice-writer`, с
  `actions/cache` на apt-пакеты, чтобы не платить полную цену на
  каждый прогон) — `_render_docx_group`/`_render_via_soffice`
  реально прогоняются в CI, не только локально. `skipif` на
  `shutil.which("soffice")` остаётся в коде тестов как defensive
  fallback (локальный дев-прогон без LibreOffice не должен падать
  красным), но в CI он больше не должен срабатывать никогда — если
  сработал, это сигнал, что apt-шаг сломался, не ожидаемое поведение.
- `Config` (`api.py`) — новые поля: `use_vlm: bool = False`,
  `vlm_model: str = "google/gemini-3-flash-preview"` (дефолт исходного
  пайплайна, «победитель пилота» его OCR+figures-задач — **временный
  placeholder до коммита §5**, не финальное решение; заменяется
  результатом A/B-калибровки на реальном corpus refigure в рамках этой
  же стадии, не выдаётся за проверенное значение до тех пор),
  `vlm_api_key: str | None = None` (фоллбэк на
  `OPENROUTER_API_KEY` env, explicit-param-overrides-env — конвенция
  типичных SDK, используется только для дефолтного `OpenRouterClient`),
  `vlm_client: VlmClient | None = None` (фоллбэк на `OpenRouterClient(
  api_key=...)`), `vlm_cache: VlmCacheBackend | None = None` (фоллбэк на
  `InMemoryCacheBackend()`).
- `ConversionResult.vlm_used` (уже есть в датаклассе с стадии 2, сейчас
  везде `False`) — становится реальным полем для docx-пути.
- **Data-egress дисциплина** (формулировка уточнена 2026-08-05):
  `use_vlm=True` отправляет через `vlm_client` **только кроп самого
  региона с фигурой** (страница/группа, обрезанная `_render_crop`/
  `_render_via_soffice`) — **не документ целиком** и не окружающий
  текст. Это гарантия самой техники (объект уже кропается ДО отправки
  по построению кода), не отдельная политика поверх — но нигде не
  написана явно на уровне читаемого API. Требование к реализации:
  `Config.use_vlm`'s docstring должен явно проговаривать оба факта —
  что отправка происходит, и что отправляется именно кроп, не весь
  документ — не полагаться на то, что это очевидно каждому вызывающему.
- Порог witness-гейта (`FIGURE_WITNESS_MIN_RECALL`) — калибруется не
  из литературы, а эмпирически, как часть §5 (см. ниже). Экспортируется
  как `Config.vlm_witness_min_recall: float = 0.80` (placeholder до
  калибровки) — раз модель теперь pluggable, разумно дать гибкость и
  здесь, не хардкодить модульную константу.

## 4. Корпус: 26 из 27 фикстур перестают быть gitignored

Решение 2026-08-05, вызвано требованием «нет untested CI path» (§3):
`libreoffice` в CI решает только половину проблемы — фикстуры остаются
недоступны в CI (`tests/integration/fixtures/*` gitignored, ~144MB, не
81MB как ошибочно фиксировала память — актуальная цифра проверена
`du`, старая исправляется отдельно). Лицензии по манифесту чистые у
26 из 27: CC BY 4.0 ×16, CC BY 3.0 IGO ×2, EU reuse right ×5, US public
domain ×3 — все стандартные, хорошо изученные для редистрибуции, у
каждой записи в `manifest.yaml` уже есть готовая строка `attribution:`.
**Единственное исключение — `iot-report-2022-national-strategies-
excerpt.docx`** (`license: unpublished — repo owner's own draft`,
authorship risk явно принят, но не разрешён, решение 2026-08-04) —
остаётся gitignored, не коммитится.

- Закрыть `.gitignore`-исключения для 26 файлов (все, кроме
  `iot-report-2022-national-strategies-excerpt.docx`), закоммитить
  бинарники (~133MB).
- Новый файл `ATTRIBUTION.md` (или расширенный `NOTICE`) — реальный
  текст, не факультативно: агрегирует уже существующие
  `manifest.yaml`'s `attribution:`-поля по группам лицензий (CC BY 4.0/
  CC BY 3.0 IGO/EU reuse right/public domain), с `source_url` на
  каждую запись. `manifest.yaml` остаётся источником истины
  (sha256/добавление даты/заметки) — `ATTRIBUTION.md` человекочитаемое
  представление ровно тех же данных для лицензионного требования
  видимости, не дублирующий источник.
- **`test_docx_groups_live.py` (порт с стадии 5) — новая целевая
  фикстура**, раз старая (`iot-report-...`) остаётся вне git:
  `efsa-echinococcus-guide.docx` (CC BY 4.0, атрибуция уже в манифесте,
  10 подтверждённых `wpg:wgp`-групп — больше всего в корпусе). Реальные
  group-id уже извлечены локально (`docx_groups.extract_and_strip_groups`,
  read-only, 2026-08-05), все `kind="group"`:
  `a106c326d0e9`, `23da97fa9020`, `179dfcb933b4`, `fde6ff90fd0c`,
  `1d84dbf972e7`, `4e3bc9ef73f2`, `559538feeabf`, `961722518e1d`,
  `41e0f316a735`, `32aee94aaaeb`. **Нюанс**: у всех 10 групп этой
  фикстуры пустые captions (`()`) — тест проверит сам механизм рендера
  (`_render_docx_group`/soffice/PDF-кроп), НЕ witness-gate (для этого
  нужен отдельный синтетический тест с непустыми captions — уже в
  `test_vlm.py`, п. Тестовое покрытие). Не путать одно с другим при
  реализации.
- `docs/execution-sequence-2026-08-04.md`/`CLAUDE.md`/
  `project_fixture_corpus` memory нужно обновить постфактум
  (`/post-merge-sync`), не сейчас — фиксируется здесь как задел.

## 5. Калибровка на реальных данных: A/B моделей + witness-порог

Обе калибровки — **один процесс, не два отдельных**: обе требуют живых
VLM-вызовов на реальном corpus, обе используют одну и ту же ручную
разметку «хорошо/плохо» как источник истины. Выполняется в рамках
стадии 4b (решение 2026-08-05), не отложено в v1.x.

**Почему не берём готовое число из литературы** — 3 независимых агента
исследовали аналогичные пороги (OCR/document-AI production-пороги —
AWS/Google/Azure/Rossum/Docling; RAG faithfulness-метрики — RAGAS/
TruLens/DeepEval/академическая литература; VLM captioning/chart-
понимание бенчмарки — CHAIR/POPE/ChartQA/CharXiv/ChartX/PlotPick).
Все три угла независимо сошлись на одном выводе: **никто не переносит
чужой порог — везде, где вообще есть содержательная рекомендация, она
про калибровку на своих размеченных данных**, не про импорт числа.
Конкретно по нашей метрике (recall уникальных слов, без precision-
члена):
- Она структурно слабее, чем то, что вообще где-либо публикует порог
  (LLM-as-judge claim-verification в RAG-мире; F1/WER-EER в document-AI;
  POPE прямо показывает: recall-без-precision выигрывается моделью,
  отвечающей "да" на всё — near-100% recall при провальном F1).
- 0.80 — правдоподобная, но не безопасная цифра: CharXiv (реальные,
  не шаблонные графики) даёт GPT-4o **84.5%** на «прочитать базовые
  элементы» — то есть даже хорошая модель на реальных данных держится
  чуть ВЫШЕ 0.80, запаса на дешёвую/маленькую модель почти нет. ChartX
  (полное извлечение «всего», ближе всего по форме задачи к нашему
  «recall всех уникальных слов») даёт GPT-4V всего **20-36%** — на
  порядок ниже, если наша задача окажется структурно ближе к этому
  классу, а не к более щадящему PlotPick (88-96%, но с 5%-допуском на
  числа и более узкой задачей).
- Вывод: диапазон возможных «правильных» значений для НАШЕЙ точной
  метрики на НАШИХ данных — от «намного строже 0.80» до «намного мягче»
  в зависимости от реальной формы задачи. Гадать бессмысленно, мерить
  дёшево (та же A/B-инфраструктура уже нужна для §5.1).

### 5.1 Отбор модели (industry-standard процесс 2026)

- **Кандидаты**: 3-4 модели через OpenRouter (сам клиент — provider-
  agnostic, см. §2, но сравнение удобнее вести через один API),
  разных семейств — не одна ценовая категория: (a) действующий
  дефолт-плейсхолдер (`google/gemini-3-flash-preview`, «победитель
  пилота» исходного пайплайна — baseline, не финал), (b) более сильная frontier-
  модель другого семейства (Claude/GPT-класс) — точность инструкций
  на структурированном mermaid-синтаксисе часто отличается от
  качества чтения самой картинки, (c) опционально — открытая модель
  через OpenRouter, для проверки «работает ли вообще без vendor
  lock-in» (смыкается с provider-agnostic принципом, п.4 обсуждения).
  Точный список фиксируется на момент исполнения (коммит из плана
  ниже) против актуального каталога OpenRouter, не жёстко здесь.
- **Eval-set**: реальные docx-фикстуры с `groups_found > 0` из
  существующего корпуса (не синтетика) — `efsa-echinococcus-guide.docx`
  (10 групп), `hackair-d7.7-pilot-evaluation.docx`, и другие с
  ненулевым groups_found по уже известным pinned-значениям
  `test_docx_corpus.py`. Ровно то «свои размеченные данные», на
  которые независимо указали все три research-агента.
- **Метрики — многоосевые, не одна цифра** (тот же вывод, что дала
  witness-gate литература: одномерная метрика — слабый гейт):
  1. Witness-recall (готовая механика) — автоматически, для каждого
     output.
  2. Numeric-defects (`format_missing_side`) — автоматически.
  3. Mermaid-render success rate (`chart_render.mermaid_renders`) —
     уже существующий гейт, бесплатно переиспользуется как метрика.
  4. **Ручная разметка на выборке** — не на всём eval-set (не
     масштабируется), а стратифицированно: все output'ы с НАИБОЛЬШИМ
     расхождением по метрикам 1-3 (именно они несут информацию) +
     небольшая случайная выборка для sanity-baseline. Простой рубрика
     pass/fail + короткий комментарий, не Likert-шкала — цель
     «находит ли recall-порог реальную границу», не полноценный UX-
     research.
  5. Стоимость/латентность на вызов — реальный, не «слепой к цене»
     выбор: тот же принцип, что применяют все обследованные
     production document-AI вендоры (не только качество).
- **Процесс**: прогнать каждую модель-кандидата на всём eval-set →
  собрать 3 автометрики + провести ручную разметку по правилу выше →
  на размеченных данных найти точку разделения recall-распределения
  между «хорошо»/«плохо» (эмпирический порог, не теоретический) →
  агрегировать по моделям с учётом стоимости → зафиксировать
  победителя как новый `Config.vlm_model` дефолт и найденный порог как
  новый `Config.vlm_witness_min_recall` дефолт.
- **Результат документируется** — не только в коде (новые дефолты), но
  и отдельной заметкой (`docs/` или memory) с таблицей
  модель×метрики×стоимость и обоснованием победителя — справочно для
  будущих пользователей пакета, не потому что дефолт железно
  зафиксирован (он остаётся override-able).
- **Предпосылка**: живой `OPENROUTER_API_KEY` — уже добавлен в
  `.env` (гитигнорится, не в репозитории) 2026-08-05.

## 6. Вне скоупа этой стадии

- Async batch-точка входа — явно отложено design-документом §3 до v1.x
  («решается когда VLM реально пойдёт в разработку... не блокирует
  дизайн API v1» — и roadmap §5 закрепляет это именно за v1.x, не 4b).
  `vlm.py` остаётся 100% sync, как ядро.
- Активация/анонс `[vlm]` в README как готовой фичи — v1.x, не сейчас.
- XLSX VLM-путь — не существует, см. §0.
- OCR/scan-VLM (`cloud_ocr.py`, второй потребитель VLM-HTTP-клиента
  в исходном пайплайне) — PDF/скан-специфика, вне проекта целиком.

## Тестовое покрытие

- `tests/unit/test_vlm_client.py` — порт `tests/unit/core/test_openrouter.py`
  (173 строки, формат-агностичный) — retry-лестница, `InbandError`,
  мокнутый `urllib.request.urlopen` (без сети).
- `tests/unit/test_vlm_cache.py` — Protocol conformance для обоих
  бэкендов, `FileCacheBackend` — реальная JSON-персистентность на
  `tmp_path`.
- `tests/unit/test_vlm.py` — селективный порт релевантного (docx-only)
  подмножества `tests/unit/convert/test_figures_vlm.py` (1079 строк в
  исходном пайплайне, в основном PDF — переносится не файл целиком, а сценарии):
  маркер-сканирование, `sanitize_vlm_markdown`, `witness_defects`,
  **cache-hit-only офлайн-путь** `enhance_docx_markdown` (тот же приём,
  что golden self-check исходного пайплайна — предзаполненный `InMemoryCacheBackend`,
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
  `project_deferred_docx_groups_live_test` memory) — **фикстура и
  group-id пересмотрены 2026-08-05** (см. §4): не
  `iot-report-2022-national-strategies-excerpt.docx` (остаётся
  gitignored), а `efsa-echinococcus-guide.docx`, 10 групп
  (`a106c326d0e9`/`23da97fa9020`/`179dfcb933b4`/`fde6ff90fd0c`/
  `1d84dbf972e7`/`4e3bc9ef73f2`/`559538feeabf`/`961722518e1d`/
  `41e0f316a735`/`32aee94aaaeb`, все `kind="group"`, уже верифицированы
  живым прогоном `extract_and_strip_groups`). `skipif` на
  `shutil.which("soffice")` остаётся как defensive fallback, но в CI
  теперь реально прогоняется (§3 — `libreoffice-writer` в `test-unit`).

## План коммитов/PR

Выполнено автономно (`/feature-workflow`, 2026-08-05) — с 3 отклонениями
от порядка ниже, зафиксированными по ходу дела как разрешённые развилки
(подробности в итоговом отчёте PR):
1) коммит 9 (расширение `Config`) физически выполнен ПЕРЕД коммитами 6-8
   — `enhance_docx_markdown()`'s сигнатура требует уже существующих
   VLM-полей `Config` для прохождения mypy strict, порядок ниже не мог
   быть буквально исполним;
2) коммиты 6/7/8 объединены в один — `vlm.py` внутренне слишком связан
   (grammar/resolution/entry-point ссылаются друг на друга), физическое
   разбиение на 3 коммита оставляло бы промежуточные состояния с mёртвыми
   импортами, не проходящими ruff;
3) README получил ещё один follow-up коммит (стиль `fixtures/README.md`/
   `conftest.py`, устаревших после коммита 1) — не было в исходном плане,
   мелкая правка того же PR.

1. `chore: un-gitignore 26 licensed corpus fixtures + add ATTRIBUTION.md`
2. `ci: install libreoffice-writer in test-unit (cached) — no untested soffice path`
3. `feat: add VlmClient protocol to api.py + refigure/vlm_client.py's OpenRouterClient (ported from core/openrouter.py)`
4. `feat: add VlmCacheBackend protocol to api.py + refigure/vlm_cache.py's in-memory/file backends`
5. `feat: widen docx_groups.extract_group_docx to accept Path | bytes`
9. `feat: extend Config with use_vlm/vlm_model/vlm_api_key/vlm_client/vlm_cache/vlm_witness_min_recall` (перемещён раньше 6-8, см. выше)
6-8. `feat: refigure/vlm.py — marker grammar, resolution, enhance_docx_markdown()` (объединены, см. выше)
10. `feat: wire vlm.enhance_docx_markdown into docx.convert() behind lazy import + use_vlm gate`
11. `feat: pyproject.toml — [vlm] extra (pdfplumber)`
12. `test: tests/unit/test_vlm_client.py — ported retry-ladder coverage`
13. `test: tests/unit/test_vlm_cache.py — protocol conformance`
14. `test: tests/unit/test_vlm.py — marker scan/sanitize/witness/offline cache-hit path`
15. `test: extend test_optional_dependency_guards.py for refigure.vlm + lazy-import-when-use_vlm=False`
16. `test: extend tests/extras/test_extras_isolation.py + ci.yml matrix — vlm leg`
17. `test: port tests/integration/test_docx_groups_live.py against efsa-echinococcus-guide.docx (soffice-gated, real in CI)`
18. `chore: A/B model comparison on real corpus — pick vlm_model + vlm_witness_min_recall defaults, document results`
19. `docs: note [vlm] extra exists, not promoted — README`

## Чек-лист реализации

- [x] 26 фикстур раскрыты из `.gitignore` + `ATTRIBUTION.md`
- [x] `libreoffice-writer` в CI (`test-unit`, кэшировано)
- [x] `VlmClient` protocol (`api.py`) + `refigure/vlm_client.py`'s `OpenRouterClient`
- [x] `VlmCacheBackend` protocol (`api.py`) + `refigure/vlm_cache.py`
- [x] `docx_groups.extract_group_docx` accepts `Path | bytes`
- [x] `refigure/vlm.py` — grammar + sanitize + witness_defects
- [x] `refigure/vlm.py` — docx image/group resolution
- [x] `refigure/vlm.py` — `enhance_docx_markdown()`
- [x] `Config` extended (включая `vlm_witness_min_recall`)
- [x] `docx.convert()` wired (lazy import, `use_vlm` gate)
- [x] `[vlm]` extra in `pyproject.toml`
- [x] `test_vlm_client.py`
- [x] `test_vlm_cache.py`
- [x] `test_vlm.py`
- [x] `test_optional_dependency_guards.py` extended
- [x] `test_extras_isolation.py` + `ci.yml` `vlm` leg
- [x] `test_docx_groups_live.py` ported (`efsa-echinococcus-guide.docx`)
- [x] A/B-калибровка проведена, `vlm_model` подтверждён/`vlm_witness_min_recall` осознанно оставлен без изменений (см. `docs/vlm-model-calibration-2026-08-05.md` — эмпирических данных для сдвига порога не нашлось, честно задокументировано, не выдумано)
- [x] README note (CLAUDE.md — намеренно не тронут, обновит `/post-merge-sync` после мержа)

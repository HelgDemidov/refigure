# Спецификация: прямые VLM-клиенты (OpenAI/Anthropic), в обход OpenRouter

**Статус:** черновик v1 · 2026-08-06
**Ветка:** `feat/vlm-direct-clients`

*Превышает целевые ≤100 строк — 2 новых клиента с разными wire-протоколами
+ отклонение от module-level guard-паттерна + пакетное решение + живая
верификация 3 облачных Claude-путей (Bedrock/Vertex/Foundry) требуют
разбора, не влезающего в норму без потери конкретики.*

## 0. Что и зачем

`witness-gate-redesign-2026-08-05.md` §6 пометил слой 2 дерева конфигурации
(«локальная модель / другой облачный провайдер») как архитектурную
возможность без готовой реализации: `VlmClient` Protocol (`refigure/
api.py`) уже провайдер-агностичен, но в комплекте один клиент —
`OpenRouterClient`. Этот спек добавляет 2 готовые реализации —
`OpenAIClient`/`AnthropicClient` в `refigure/vlm/client.py` — закрывающие
и локальный инференс (confidentiality-драйвер), и прямой доступ к облаку
в обход OpenRouter как единой точки отказа. `AnthropicClient` дополнительно
спроектирован так, что Claude через Bedrock/Vertex/Foundry (Azure) доступны
инъекцией готового клиента (§2) — В СКОУПЕ этого PR: по одному живому
интеграционному тесту и докстринг-рецепту на каждое облако (§3). Явно вне
скоупа: сопровождение актуальности их model-ID/lifecycle (это забота
каждой платформы, не `refigure`), Google Gemini direct, гетерогенная
панель судей — см. «Вне скоупа».

Архитектура согласована в диалоге (не веб-поиском, а живой проверкой PyPI/
GitHub/офиц. документации 2026-08-06): LiteLLM отклонён (supply-chain
компрометация PyPI март 2026, CVE-2026-42271/CVE-2026-42208 — RCE/SQLi,
оба в CISA KEV, оба формально Proxy-only, но неясный SDK-scope мартовского
инцидента + частота — повод не рисковать); `aisuite` отклонён (последний
релиз PyPI 2025-11-25, ~9 мес. без обновлений, нет подтверждённой
поддержки vision).

## 1. `OpenAIClient` — прямой OpenAI + любой OpenAI-совместимый эндпоинт

Новый класс в `refigure/vlm/client.py`, поверх пакета `openai` (не
хендролл `urllib`, в отличие от `OpenRouterClient` — обоснование: SDK даёт
типизированный клиент + собственный retry, обслуживающий и OpenAI, и
Ollama/vLLM/LM Studio одним кодом, т.к. все три говорят на одном
`/v1/chat/completions` диалекте — подтверждено официальной документацией
Ollama, `docs.ollama.com/api/openai-compatibility`: `OpenAI(base_url=
"http://localhost:11434/v1/", api_key="ollama")`).

```python
class OpenAIClient:
    def __init__(
        self, api_key: str | None = None, *, base_url: str | None = None,
        image_content_format: Literal["dict", "string"] = "dict",
        max_tokens: int = 8000, timeout: float = 1800.0,
    ) -> None: ...
    def send(self, prompt: str, image_uri: str, *, model: str) -> str: ...
```

`image_content_format="string"` — обязателен при `base_url` на Ollama:
её OpenAI-compat vision ждёт `"image_url": "data:image/png;base64,..."`
(голая строка), а не `{"url": "..."}` (вложенный словарь, формат
настоящего OpenAI и `OpenRouterClient`) — подтверждено `docs.ollama.com`
живьём, не по памяти. Не автоопределяется по `base_url` (хрупкая
эвристика на URL-паттерн) — явный параметр конструктора, тот же принцип,
что уже применён к `vlm_judge_mode` (явный `Literal`-fork вместо неявного
вывода).

## 2. `AnthropicClient` — прямой Anthropic

Новый класс, поверх пакета `anthropic`. Messages API структурно другой,
не вариация OpenAI-формата: эндпоинт `/v1/messages`, заголовки `x-api-key`
(не `Authorization: Bearer`) + обязательный `anthropic-version`;
контент-блок картинки — `{"type": "image", "source": {"type": "base64",
"media_type": ..., "data": "<base64 без "data:" префикса>"}}`; ответ —
`response.content[0].text`, не `choices[0].message.content`.

```python
class AnthropicClient:
    def __init__(
        self, api_key: str | None = None, *,
        client: "anthropic.Anthropic | None" = None,
        max_tokens: int = 8000, timeout: float = 1800.0,
    ) -> None:
        # client задан -> используется как есть (см. ниже); иначе строится
        # anthropic.Anthropic(api_key=api_key)
        ...
    def send(self, prompt: str, image_uri: str, *, model: str) -> str:
        # парсит image_uri "data:<media_type>;base64,<data>" на 2 поля
        ...
```

`client=` — инъекция уже готового anthropic-клиента вместо голого
`api_key`. Обоснование (проверено живьём, `platform.claude.com`,
2026-08-06): `AnthropicBedrock`/`AnthropicVertex`/`AnthropicFoundry` — те
же классы пакета `anthropic`, с идентичным `.messages.create()`
интерфейсом, отличаются только тем, чем сконструированы (AWS SigV4-креды/
бирер-токен, Google ADC/service account, Azure API-key/Entra ID
соответственно). При `client=AnthropicBedrock(aws_region=...)` (или
Vertex/Foundry-эквивалент) `AnthropicClient.send()` не меняется ни
строкой — вся auth/endpoint-специфика инкапсулирована внутри
конструктора переданного объекта, не в `refigure`. Это делает
Bedrock/Vertex/Foundry доступными без отдельных классов под каждый —
живая проверка и документация для всех трёх добавлены в скоуп, §3.

Модель-ID у Anthropic голый (`"claude-haiku-4-5-20251001"`), без
`anthropic/`-префикса — **не совместим** с сегодняшними дефолтами
`vlm_model`/`vlm_judge_model`/`vlm_judge_panel` (OpenRouter-слаг-формат).
Пользователь, переключивший `vlm_client=AnthropicClient(...)`, обязан
также переопределить эти строки — задокументировать явно в докстринге
каждого поля `Config`, не полагаться на то, что это очевидно.

## 3. Bedrock/Vertex/Foundry — живая проверка + документация

Раз механизм `client=` (§2) уже покрывает все три структурно, в скоуп
добавляется по одному живому интеграционному тесту и по одному
докстринг-рецепту на каждое облако. Явно НЕ в скоупе (см. «Вне скоупа»):
любое сопровождение актуальности их model-ID — lifecycle (Deprecated/
Retired) независимо ведёт каждая платформа сама, не `refigure`.

**Живые тесты** — `tests/integration/test_anthropic_{bedrock,vertex,
foundry}_live.py`, по прецеденту `test_docx_groups_live.py` (`_live`-
суффикс, `skipif`-гейт, `skip` не `fail` без окружения). Не гейтятся на
«есть ли амбиентные креды на машине» — наличие AWS/GCP/Azure credentials
для чего-то другого не согласие тратить реальные деньги на реальном
enterprise-облаке. Каждый требует ЯВНОГО opt-in флага:

- `REFIGURE_LIVE_BEDROCK=1` (+ обычный AWS credential chain или bearer-
  токен)
- `REFIGURE_LIVE_VERTEX=1` (+ `GOOGLE_CLOUD_PROJECT` + ADC)
- `REFIGURE_LIVE_FOUNDRY=1` (+ `ANTHROPIC_FOUNDRY_API_KEY`/
  `ANTHROPIC_FOUNDRY_RESOURCE`)

Ни один не входит в `.github/workflows/ci.yml` — CI сегодня не хранит ни
одного облачного секрета ни для одного VLM-клиента (проверено: живые
проверки в этом проекте до сих пор были только ручными, включая
`OpenRouterClient`'s собственные smoke-тесты при реализации PR #11).
Заводить 3 отдельных enterprise-аккаунта + секреты в CI — отдельное
решение с постоянной стоимостью, не часть этого спека; тесты запускаются
вручную разработчиком перед мержем, тем же способом.

**Документация** — по одному короткому рецепту в докстринге
`AnthropicClient` (конструктор целевого клиента + пример `vlm_model` в
формате нужной платформы: Bedrock — `"global.anthropic.claude-opus-4-6-
v1"`, Vertex — `"claude-opus-5"`, Foundry — deployment-имя, по умолчанию
совпадающее с модель-ID). Иллюстративные примеры, не проверяемые на
актуальность — устаревание конкретного ID у платформы не баг `refigure`.

### Доступ к аккаунтам для ручного прогона живых тестов (проверено 2026-08-06)

Карта обязательна на всех трёх (даже там, где не списывают) — «бесплатно»
не значит «без карты». Бесплатный кредит реально покрывает тест не везде:

| Облако | Регистрация | Реальный тест Claude бесплатно? |
|---|---|---|
| AWS Bedrock | Карта обязательна, но не списывают | **Да** — покрывается $200-кредитом новых аккаунтов (6 мес.), Bedrock явно не исключён |
| GCP Vertex | Карта обязательна | Нет — $300-триал explicitly исключает generative AI partner-модели «как managed API», Claude прямо в их числе; спишется с карты (доли цента) |
| Azure Foundry | Карта обязательна | Нет — Claude на Foundry биллится как Marketplace-позиция (Claude Consumption Units), исключён из $200 free-account кредита; спишется с карты (доли цента). Отдельная ловушка: каталог Foundry не помечает Marketplace-модели визуально — искать коллекцию **"Direct from Azure"** для кредит-покрываемых моделей, Claude туда не входит (задокументированные жалобы на Microsoft Q&A о неожиданных счетах) |

**AWS — единственный кандидат для действительно бесплатного прогона**
из трёх; GCP/Azure технически по-прежнему копеечные (один тестовый вызов
на Haiku-уровне — доли цента), но не покрываются промо-кредитом,
спишутся с реальной карты.

## 4. Импорт-гварды — на уровне класса, не модуля

`refigure/vlm/client.py` уже используется без единой сторонней
зависимости (`OpenRouterClient` — чистый `urllib`) — это свойство
обязано сохраниться: `import refigure.vlm.client` не должен требовать
`openai`/`anthropic`, только сама конструкция `OpenAIClient()`/
`AnthropicClient()`. Отличается от установленного паттерна (`xlsx/
__init__.py`'s module-level `try/except` до любого same-package импорта):
здесь guard — `try/except ImportError` внутри `__init__` каждого класса,
поднимает `MissingOptionalDependencyError` с сообщением вида
`"refigure[vlm-direct] is required to use OpenAIClient"`. Обосновать это
отклонение явно в докстринге модуля — это НЕ отказ от паттерна, а его
адаптация к файлу с несколькими независимыми классами разных
зависимостей, а не одной капабилити на модуль.

## 5. Упаковка — новый саб-экстра `vlm-direct`

`pyproject.toml`: `vlm-direct = ["refigure[vlm]", "openai>=2.53,<3",
"anthropic>=0.120,<1"]` — НЕ внутрь существующего `[vlm]` (сегодня 0
HTTP-зависимостей, `pdfplumber` — только LibreOffice-кроп; `[vlm]` обязан
остаться нулевым для дефолтного `OpenRouterClient`-сценария). Один
совмещённый экстра, не `vlm-openai`+`vlm-anthropic` раздельно: пакеты
делят 7 из 8 обязательных зависимостей (`httpx`/`pydantic`/`anyio`/
`distro`/`sniffio`/`jiter`/`typing-extensions`, проверено живьём через
PyPI JSON API) — разделение почти не экономит вес, но удваивает
CI-матрицу.

**Реальный баг, найденный и исправленный при реализации (2026-08-06), тот
же класс, что и `docx_groups.py`-инцидент** (`project_extras_isolation_bug`
memory): `refigure/vlm/client.py` — подмодуль пакета `refigure.vlm`, а
импорт любого подмодуля всегда сначала прогоняет `refigure/vlm/
__init__.py`, где есть модульный `pdfplumber`-гвард. Значит
`from refigure.vlm.client import OpenAIClient` падал с
`"refigure[vlm] is required"` ещё до собственного гварда `OpenAIClient`,
даже если `vlm-direct` был установлен изолированно. Воспроизведено живьём
в чистом venv (`uv venv` + `uv pip install ".[vlm-direct]"` без `[vlm]`)
до фикса, подтверждено зелёным после. В отличие от `docx_groups.py` —
это НЕ случайная связка для обхода: единственная реальная точка
использования `Config.vlm_client` (`enhance_docx_markdown`) всегда рендерит
фигуру через `pdfplumber` ПЕРЕД тем, как передать её любому `VlmClient` —
так что зависимость `vlm-direct` → `[vlm]` честная, не костыль. Фикс —
self-referential extra (`"refigure[vlm]"` в списке `vlm-direct`), не
вынос `client.py` из пакета. Регрессия закрыта: `("refigure.vlm.client",
"pdfplumber")` добавлен в `_POISON_CASES`
(`tests/unit/test_optional_dependency_guards.py`).

## 6. Выбор клиента — без новых полей `Config`

`Config.vlm_client=OpenAIClient(...)`/`AnthropicClient(...)` — уже
существующий механизм (`api.py`, тот же паттерн, что `VlmCacheBackend`'s
2 готовые реализации `InMemoryCacheBackend`/`FileCacheBackend`). Никакого
`vlm_provider`-поля, никаких per-provider `vlm_*_api_key` полей — это
сознательный выбор (не забытая доработка): `Config.vlm_api_key`'s
докстрока уже говорит «Ignored when `vlm_client` is set», распространяем
тот же контракт на новые клиенты без изменений.

`Config.use_vlm`'s докстрока («...OpenRouter by default») требует правки:
данные уходят туда, куда указывает выбранный `vlm_client`, не всегда
OpenRouter.

## Тестовое покрытие

- Юнит-тесты `OpenAIClient`/`AnthropicClient` — мокать `openai`/
  `anthropic` SDK-вызовы (не реальный HTTP), включая
  `image_content_format` обе ветки и `data:`-URI парсинг Anthropic-стороны
  (валидный вход + один edge case на отсутствие `;base64,`).
- `AnthropicClient`'s `client=` инъекция — тест, что переданный
  fake-объект (протокольно похожий на `anthropic.Anthropic`, с тем же
  `.messages.create()`) используется как есть, `api_key`/конструктор
  `anthropic.Anthropic()` не вызывается вторично поверх него.
- Новый тест-файл (не `_POISON_CASES` — тот проверяет **импорт модуля**,
  здесь гвард на **конструкцию класса**, другой уровень): конструирование
  `OpenAIClient()`/`AnthropicClient()` без `openai`/`anthropic`
  установленных поднимает `MissingOptionalDependencyError`, не
  `ModuleNotFoundError`, и что `refigure.vlm.client` **успешно
  импортируется** в их отсутствие (регрессионный тест самого свойства
  из §4).
- `.github/workflows/ci.yml`'s `test-extras` matrix: новый leg
  `vlm-direct` (`uv pip install ".[vlm-direct]"`) — проверить оба триггера
  из `project_extras_isolation_bug` memory (import-order, package-nesting)
  применимы ли к новому коду; вероятно нет (класс-уровневый гвард снимает
  оба класса проблем по конструкции), но проверить эмпирически, не
  постулировать.
- 3 живых интеграционных теста (§3) — по одному на Bedrock/Vertex/
  Foundry, `skipif`-гейт на явный opt-in флаг (`REFIGURE_LIVE_BEDROCK`/
  `_VERTEX`/`_FOUNDRY`), запускаются вручную, не в CI.
- Обновить `docs.md`/докстроки, ссылающиеся на «OpenRouter by default».

## План коммитов/PR

1. `feat: add OpenAIClient (openai SDK, base_url-configurable)`
2. `feat: add AnthropicClient (anthropic SDK, Messages API translation + injectable client=)`
3. `test: unit coverage for both clients + class-level guard tests`
4. `chore: add vlm-direct extra to pyproject.toml`
5. `ci: add vlm-direct leg to test-extras matrix`
6. `docs: update Config docstrings (use_vlm egress, model-ID coupling)`
7. `test: live opt-in integration test for AnthropicClient via Bedrock`
8. `test: live opt-in integration test for AnthropicClient via Vertex`
9. `test: live opt-in integration test for AnthropicClient via Foundry`
10. `docs: add Bedrock/Vertex/Foundry recipes to AnthropicClient docstring`

## Чек-лист реализации

- [x] `OpenAIClient` реализован (коммит 1)
- [x] `AnthropicClient` реализован, включая `client=` инъекцию (коммит 2)
- [x] юнит-тесты обоих клиентов + класс-уровневые guard-тесты (коммит 3)
- [x] `vlm-direct` extra в `pyproject.toml` (коммит 4)
- [x] `test-extras` CI leg добавлен, проверен зелёным (коммит 5)
- [x] докстроки `Config`/`refigure/vlm/client.py` обновлены (коммит 6)
- [x] живой тест написан + skip-путь проверен вручную — Bedrock (коммит 7; реальный прогон против AWS не выполнялся — нет доступа к AWS-аккаунту в этом окружении, см. отчёт)
- [x] живой тест написан + skip-путь проверен вручную — Vertex (коммит 8; реальный прогон против GCP не выполнялся — нет доступа к GCP-проекту в этом окружении)
- [x] живой тест написан + skip-путь проверен вручную — Foundry (коммит 9; реальный прогон против Azure не выполнялся — нет доступа к Azure-подписке в этом окружении)
- [x] докстринг-рецепты для всех трёх облаков (коммит 10)

## Вне скоупа

- Google Gemini direct (не Claude-через-Vertex), любой не-Anthropic
  Bedrock-модельный ряд (Titan/Nova/Llama и т.п.) — у каждого свой wire-
  формат внутри Bedrock/Vertex, не переиспользует `anthropic`-пакет;
  не запрошено, нет готового драйвера сопоставимого с local/OpenRouter-
  independence.
- **Сопровождение актуальности model-ID/lifecycle** для Bedrock/Vertex/
  Foundry (Deprecated/Retired-графики, независимые у каждой платформы) —
  явное решение: докстринг-примеры (§3) иллюстративны и НЕ проверяются
  на актуальность; устаревание конкретного ID у платформы — не баг
  `refigure`, и не повод заводить внутренний реестр моделей.
- Автоматизация 3 живых тестов (§3) в CI — остаются ручными
  (`REFIGURE_LIVE_*`-гейт), заводить постоянные enterprise-cloud
  секреты/аккаунты в CI — отдельное решение, не часть этого спека.
- Гетерогенная панель судей (`vlm_judge_panel` на разных клиентах) —
  остаётся на одном `client`-инстансе, как сегодня.
- Автовыбор `image_content_format` по `base_url`-эвристике.

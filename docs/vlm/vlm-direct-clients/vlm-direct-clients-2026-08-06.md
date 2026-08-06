# Спецификация: прямые VLM-клиенты (OpenAI/Anthropic), в обход OpenRouter

**Статус:** черновик v1 · 2026-08-06
**Ветка:** `feat/vlm-direct-clients`

*Превышает целевые ≤100 строк (171) — 2 новых клиента с разными wire-
протоколами + отклонение от module-level guard-паттерна + пакетное
решение требуют разбора, не влезающего в норму без потери конкретики.*

## 0. Что и зачем

`witness-gate-redesign-2026-08-05.md` §6 пометил слой 2 дерева конфигурации
(«локальная модель / другой облачный провайдер») как архитектурную
возможность без готовой реализации: `VlmClient` Protocol (`refigure/
api.py`) уже провайдер-агностичен, но в комплекте один клиент —
`OpenRouterClient`. Этот спек добавляет 2 готовые реализации —
`OpenAIClient`/`AnthropicClient` в `refigure/vlm/client.py` — закрывающие
и локальный инференс (confidentiality-драйвер), и прямой доступ к облаку
в обход OpenRouter как единой точки отказа. Не в скоупе: Google/Vertex
direct, Bedrock, гетерогенная панель судей — см. «Вне скоупа».

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
        max_tokens: int = 8000, timeout: float = 1800.0,
    ) -> None: ...
    def send(self, prompt: str, image_uri: str, *, model: str) -> str:
        # парсит image_uri "data:<media_type>;base64,<data>" на 2 поля
        ...
```

Модель-ID у Anthropic голый (`"claude-haiku-4-5-20251001"`), без
`anthropic/`-префикса — **не совместим** с сегодняшними дефолтами
`vlm_model`/`vlm_judge_model`/`vlm_judge_panel` (OpenRouter-слаг-формат).
Пользователь, переключивший `vlm_client=AnthropicClient(...)`, обязан
также переопределить эти строки — задокументировать явно в докстринге
каждого поля `Config`, не полагаться на то, что это очевидно.

## 3. Импорт-гварды — на уровне класса, не модуля

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

## 4. Упаковка — новый саб-экстра `vlm-direct`

`pyproject.toml`: `vlm-direct = ["openai>=2.53,<3", "anthropic>=0.120,<1"]`
— НЕ внутрь существующего `[vlm]` (сегодня 0 HTTP-зависимостей,
`pdfplumber` — только LibreOffice-кроп; `[vlm]` обязан остаться нулевым
для дефолтного `OpenRouterClient`-сценария). Один совмещённый экстра, не
`vlm-openai`+`vlm-anthropic` раздельно: пакеты делят 7 из 8 обязательных
зависимостей (`httpx`/`pydantic`/`anyio`/`distro`/`sniffio`/`jiter`/
`typing-extensions`, проверено живьём через PyPI JSON API) — разделение
почти не экономит вес, но удваивает CI-матрицу.

## 5. Выбор клиента — без новых полей `Config`

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
- Новый тест-файл (не `_POISON_CASES` — тот проверяет **импорт модуля**,
  здесь гвард на **конструкцию класса**, другой уровень): конструирование
  `OpenAIClient()`/`AnthropicClient()` без `openai`/`anthropic`
  установленных поднимает `MissingOptionalDependencyError`, не
  `ModuleNotFoundError`, и что `refigure.vlm.client` **успешно
  импортируется** в их отсутствие (регрессионный тест самого свойства
  из §3).
- `.github/workflows/ci.yml`'s `test-extras` matrix: новый leg
  `vlm-direct` (`uv pip install ".[vlm-direct]"`) — проверить оба триггера
  из `project_extras_isolation_bug` memory (import-order, package-nesting)
  применимы ли к новому коду; вероятно нет (класс-уровневый гвард снимает
  оба класса проблем по конструкции), но проверить эмпирически, не
  постулировать.
- Обновить `docs.md`/докстроки, ссылающиеся на «OpenRouter by default».

## План коммитов/PR

1. `feat: add OpenAIClient (openai SDK, base_url-configurable)`
2. `feat: add AnthropicClient (anthropic SDK, Messages API translation)`
3. `test: unit coverage for both clients + class-level guard tests`
4. `chore: add vlm-direct extra to pyproject.toml`
5. `ci: add vlm-direct leg to test-extras matrix`
6. `docs: update Config docstrings (use_vlm egress, model-ID coupling)`

## Чек-лист реализации

- [ ] `OpenAIClient` реализован (коммит 1)
- [ ] `AnthropicClient` реализован (коммит 2)
- [ ] юнит-тесты обоих клиентов + класс-уровневые guard-тесты (коммит 3)
- [ ] `vlm-direct` extra в `pyproject.toml` (коммит 4)
- [ ] `test-extras` CI leg добавлен, проверен зелёным (коммит 5)
- [ ] докстроки `Config`/`refigure/vlm/client.py` обновлены (коммит 6)

## Вне скоупа

- Google/Vertex direct, любой не-Anthropic Bedrock — не запрошено, нет
  готового драйвера сопоставимого с local/OpenRouter-independence.
- Claude через `AnthropicBedrock`/`AnthropicVertex` (тот же пакет
  `anthropic`, естественное расширение) — упомянуть как задел, не
  реализовывать.
- Гетерогенная панель судей (`vlm_judge_panel` на разных клиентах) —
  остаётся на одном `client`-инстансе, как сегодня.
- Готовый embedded-inference (не HTTP) локальный клиент.
- Автовыбор `image_content_format` по `base_url`-эвристике.

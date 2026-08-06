# Спецификация: релиз-гейт + PyPI-паблиш (стадия 8)

**Статус:** черновик v1 · 2026-08-06
**Ветка:** `fix/release-gate`

> Превышает целевые ≤100 строк — совмещает найденный вживую packaging-баг
> (§1), новый CI-механизм (§2) и явное разделение «что автоматизируем» vs
> «что решает пользователь вручную» (§4) — сжатие потеряло бы именно эту
> границу, а она здесь самое важное.

## 0. Что и зачем

`execution-sequence-2026-08-04.md`, стадия 8 (~5%): «гейт-чеклист + механика
паблиша» — два разных куска. Этот спек строит МЕХАНИКУ (код: sdist-скоуп,
build-верификация, publish-workflow) и явно перечисляет ГЕЙТ (чек-лист
предусловий, включая нужные, но некодовые действия — версия/публичность
репо/branch protection). Версия пакета и момент перехода репо в публичное —
**открытые решения пользователя**, этот спек их не принимает, только
готовит инфраструктуру под любое из них.

Заземление — три факта, проверенные вживую в этой сессии, не по памяти:
1. `python -m build` уже сегодня собирает чистый wheel (72KB,
   `[tool.hatch.build.targets.wheel] packages = ["refigure"]`, PR #1-2) —
   без изменений.
2. **sdist раздувается до 129MB** — у sdist-таргета нет include/exclude,
   hatchling по умолчанию тащит весь VCS-tracked репозиторий: 144MB
   корпуса (`tests/integration/fixtures/`), `.claude/`, `.hypothesis/`,
   `docs/`. У PyPI мягкий лимит ~100MB на sdist (выше — вручную к админам
   PyPI). Реальный баг, не был в чек-листе раньше.
3. `.github/workflows/` содержит только `ci.yml` — паблиш-воркфлоу нет
   вообще; `test-extras`-матрица ставит пакет из локального исходника
   (editable), не из собранного артефакта — деградация wheel/sdist прямо
   сейчас никак не поймалась бы в CI.

## 1. Fix: скоуп sdist

`pyproject.toml`, `[tool.hatch.build.targets.sdist]` — explicit include, по
образцу того, что уже сделал wheel-таргет (`packages = ["refigure"]`), но
для sdist нужен более широкий набор (исходники тестов имеют право ехать в
sdist — обычная практика, позволяет пересобрать/перетестировать без git):
`refigure/`, `tests/unit/`, `tests/support.py`, `tests/__init__.py`,
`README.md`, `LICENSE`, `NOTICE`, `ATTRIBUTION.md`, `pyproject.toml`.
Явно ИСКЛЮЧить: `tests/integration/` (144MB бинарного корпуса — интеграционные
тесты и так требуют git-чекаута для фикстур, sdist им не нужен),
`docs/`, `.claude/`, `.hypothesis/`, `scripts/`, `examples/`. Проверка —
`tar -tzf dist/*.tar.gz | wc -l`/размер до и после, руками в момент
реализации (не тест — packaging-конфиг, тестировать имеет смысл сборкой,
не unit-тестом).

## 2. Build-верификация — новый job в `ci.yml`, always-on

Не паблиш (тот — только по тегу, §3), а дешёвая проверка на каждый push/PR,
ловит именно тот класс бага, что нашёлся в §1: `python -m build` →
`twine check dist/*` (метаданные валидны) → `pip install
dist/*.whl` в свежий venv → `refigure --version` (smoke: пакет
реально импортируется и CLI работает из СОБРАННОГО артефакта, не из
editable-исходника, чего сегодня не проверяет ни один существующий job).
Держит sdist-размер под явным порогом (`test -lt 5MB` или около, с
комментарием почему) — регрессия вида §1 падает громко, не молча.

## 3. Publish workflow — `.github/workflows/publish.yml` (новый)

PyPI trusted publishing (OIDC, без хранимых токенов/секретов) —
[текущий рекомендуемый PyPI-механизм]. Триггер — `push: tags: ["v*"]` (не
`release:` — тег проще создать локально/скриптом, не требует отдельного
шага «создать GitHub Release» до паблиша). Отдельный файл от `ci.yml`,
не job в нём: у паблиша `permissions: id-token: write` — минимизировать
blast radius этого разрешения, не расширять его на весь основной CI-файл.
Шаги: checkout → `python -m build` → `pypa/gh-action-pypi-publish` (без
`password:`/`api-token:” — OIDC делает это за счёт `id-token: write` +
GitHub Environment). Требует on-site настройки (не код, см. §4 п.4).

## 4. Гейт релиза (чек-лист предусловий, не код)

Из `execution-sequence` + `CLAUDE.md` §Git workflow:

- [x] 4 (перевод на английский) — PR #6.
- [x] 5 (тесты зелёные) — 293 unit + 51 integration, 3 skip (live-VLM,
  ожидаемо).
- [x] 6 (CI-матрица extras) — PR #8, 7 legs.
- [x] 6b (CLI протестирован) — PR #9.
- [x] 7 (README) — PR #13 (после мержа).
- [ ] **Номер версии** — `0.0.0` → реальный semver. Открытый вопрос
  пользователю: `0.1.0` (осторожный старт, API может ещё сдвинуться) vs
  `1.0.0` (проект везде называет себя «v1» — семантическое несоответствие,
  если релиз выйдет как 0.x). Не решается этим спеком.
- [ ] **Репозиторий публичный** — сейчас private, `branches/main/protection`
  403-ит (GitHub требует Pro либо публичный репо для этой фичи на
  приватных репо). Публичность — предпосылка СЛЕДУЮЩЕГО пункта, не
  наоборот; момент перехода — решение пользователя (может быть прямо
  перед тегом релиза, не обязан идти заранее).
- [ ] **Branch protection на `main`** (no-force-push/no-deletion/
  required-status-checks) — блокирован предыдущим пунктом.
- [ ] **PyPI trusted publisher настроен** — pending-publisher на стороне
  PyPI (проект `refigure` уже застолблён как `0.0.0`-плейсхолдер, см.
  README) должен указывать на `HelgDemidov/refigure`, workflow-файл
  `publish.yml`, GitHub Environment (рекомендуется завести `pypi`-
  environment в Settings — protection rule опционален, но сам факт
  named environment требуется trusted-publishing'ом PyPI). Ручная настройка
  на сайте PyPI, не код.
- [ ] Тег `vX.Y.Z` создан и запушен → триггерит §3.

## Тестовое покрытие

- Новый CI job (§2) — build-верификация, always-on, не отдельный pytest-файл
  (проверяет packaging-конфиг, не код `refigure/`).
- Ручная проверка при реализации: `tar -tzf dist/*.tar.gz` до/после fix §1 —
  подтвердить размер упал с 129MB до низких единиц MB.
- `publish.yml` (§3) не тестируется автоматически до первого реального
  тега — риск принят: OIDC trusted-publishing — decoupled от кода,
  ошибка конфигурации проявится один раз при первом реальном релизе, не
  раньше.

## План коммитов/PR

1. `docs: draft spec for release-gate` (этот коммит, `/tech-spec`)
2. `fix: scope sdist to package essentials via [tool.hatch.build.targets.sdist]`
3. `ci: add build-verification job — build, twine check, install-from-wheel smoke test`
4. `ci: add publish.yml — PyPI trusted publishing on version tag push`

## Чек-лист реализации

- [ ] `[tool.hatch.build.targets.sdist]` include/exclude, sdist-размер
  подтверждён (было 129MB)
- [ ] `ci.yml` — build-верификация job
- [ ] `.github/workflows/publish.yml`
- [ ] Ручные шаги §4 (версия/публичность/protection/pending-publisher/тег) —
  вне этого PR, выполняются пользователем после мержа, при готовности

## Вне скоупа

- Сам номер версии, сам факт перехода репо в публичное, сама настройка
  branch protection и PyPI pending-publisher — не код, не автоматизируются
  этим PR, см. §4.
- TestPyPI dry-run автоматизация — не заведена; первый реальный тег идёт
  сразу на PyPI, риск принят (проект уже свежий 0.0.0-плейсхолдер, не
  занятое чужими данными пространство).
- GitHub Release notes/changelog-автоматизация — отдельная, не связанная с
  паблиш-механикой задача, не в этом спеке.

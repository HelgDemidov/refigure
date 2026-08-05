# Спецификация: переработка witness-гейта (`token_recall` + mermaid-уместность)

**Статус:** черновик v1 · 2026-08-05
**Ветка:** `feat/witness-gate-redesign`

> Не строго ≤100 строк — переработка затрагивает кэш-формат и требует
> плана валидации на реальных данных, сжатие потеряло бы проверяемость.

## 0. Что и зачем

A/B-калибровка (`docs/vlm/vlm-model-calibration/vlm-model-calibration-2026-08-05.md`,
24 живых вызова) нашла 2 независимых, задокументированных пробела в
существующем witness-гейте (`refigure/vlm.py`'s `witness_defects`/
`token_recall` + `chart_render.mermaid_renders()`):

1. **`token_recall` языково-слеп.** На многоязычном eval-set recall разошёлся
   0.354–1.00 не по точности модели, а по тому, перевела ли она подписи на
   английский — модель без единой фактической ошибки (Gemini) получила
   ХУДШИЙ recall, чем модель с реальной ошибкой (Claude Haiku, подмена
   «Облачный»→«Областной»).
2. **`mermaid_renders()` проверяет только синтаксис, не уместность.**
   GPT-4o-mini сфабриковал неуместную mermaid-диаграмму в 8 из 8 реальных
   кропов (100%, воспроизводимо) — ни разу не поймано, диаграмма всегда
   синтаксически валидна.

Изучены индустриальные альтернативы (RAGAS/DeepEval claim-decomposition,
embedding-метрики типа BERTScore/multilingual CLIPScore, SelfCheckGPT
self-consistency) — обоснование выбора см. §2 и «Вне скоупа». Скоуп —
весь witness-механизм целиком (не только `token_recall`), только DOCX
(как и весь `vlm.py` — у `.xlsx` VLM-пути нет).

## 1. Бесплатный путь (`vlm_verify=False`, по умолчанию) — код не меняется

`token_recall`/`numeric_counter`/`format_missing_side` остаются как есть:
они уже ортогональны друг другу (числовая проверка не зависит от
word-recall), а числовой сигнал уже язык-независим и работает корректно
независимо от «слепоты» word-recall — эмпирически подтверждено (ни одного
`figure-witness-numeric`-ложного срабатывания за оба раунда калибровки).
Решение (2026-08-05, явное): НЕ добавлять embedding-метрику
(BERTScore/multilingual-гибриды) ради языковой терпимости — требует
тяжёлой ML-зависимости (`sentence-transformers`/`torch`, сотни МБ),
противоречит per-capability-лёгкой философии проекта. Вместо этого —
честная документация ограничения (см. §4).

## 2. Платный opt-in путь (`vlm_verify=True`) — новый `judge_defects()`

`Config.vlm_verify: bool = False` (`api.py`, новое поле рядом с
остальными `vlm_*`). Один дополнительный `VlmClient.send()`-вызов на
маркер (та же картинка + уже сгенерированное описание) с **дискриминативными
да/нет-вопросами**, не повторной генерацией — по исследованию
Generative-Discriminative Gap (VLM надёжнее отвечает на конкретный вопрос
про своё же описание, чем генерирует точный текст заново):

```
hallucination: yes|no — упоминает ли описание объект/связь/число,
  которых нет на картинке?
mermaid_fit: yes|no|n/a — если есть ```mermaid, действительно ли ЭТОТ
  тип диаграммы подходит форме фигуры? n/a, если mermaid нет ИЛИ фигура
  корректно не подошла ни под одну категорию промпта.
language: yes|no — написано ли по-английски (транскрибированные ярлыки
  оригинала не в счёт)?
```

`vlm.judge_defects(image_uri: str, response: str, *, client: VlmClient,
model: str) -> list[str]` — построчный парсинг фиксированного формата в
0-3 строки-дефекта (`vlm-judge-hallucination: ...`/`vlm-judge-mermaid:
...`/`vlm-judge-language: ...`); нераспознанный формат ответа — warning,
не исключение (тот же «сигнал, не отказ» принцип, что у `witness_defects`).

Вызывается из `enhance_docx_markdown` для ОБОИХ типов маркеров — не
только групп (в отличие от `witness_defects`, который требует caption-
свидетеля и поэтому group-only): `judge_defects` смотрит на саму картинку,
свидетель не нужен, поэтому применим и к standalone-изображениям —
устраняет для этого класса дефектов пробел, который у `witness_defects`
был архитектурным решением (не багом), см. `vlm.py`'s текущую докстроку.
Порог/threshold не нужен — да/нет-вердикт не требует калибровки числа
(в отличие от `vlm_witness_min_recall`) — не плодим второй магический
порог, который тоже пришлось бы откалибровывать.

## 3. Кэш — новое опциональное поле `judge_verdict`

Формат записи кэша (`vlm_cache.py`'s backends не меняются — они уже
хранят произвольный `dict[str, object]`, это чисто `vlm.py`-уровневое
изменение формы записи): `{"model": str, "markdown": str,
"judge_verdict": list[str] | None}` — `None`/отсутствует = ещё не
проверено judge'ем.

Логика в `enhance_docx_markdown`: `vlm_verify=False` → `judge_defects`
не вызывается НИКОГДА, независимо от состояния кэша (офлайн-гарантия
существующего теста не нарушается). `vlm_verify=True` + cache-hit БЕЗ
`judge_verdict` (запись создана раньше без verify) → досчитываем ТОЛЬКО
judge-вызов (описание уже офлайн из кэша), дописываем `judge_verdict` в
ту же запись. Явно переформулированная офлайн-гарантия: «cache-hit
офлайн, если verdict уже есть или verify выключен» — не только «если
markdown в кэше».

## 4. Документация ограничения (докстроки)

`Config.vlm_witness_min_recall` и `witness_defects` (`vlm.py`) получают
явную формулировку: word-recall язык-чувствителен, на неанглоязычном
исходнике низкий recall может означать «не перевёл», а не «ошибся» —
см. `docs/vlm/vlm-model-calibration/vlm-model-calibration-2026-08-05.md`.
Реальное решение для многоязычных документов — включить `vlm_verify`
(`judge`'s `language`-вопрос ловит это напрямую, `hallucination`-вопрос
не зависит от языка вовсе, поскольку сверяется с картинкой, а не с
текстом свидетеля).

## 5. Валидация перед мержем — на уже размеченных данных, не вслепую

Тот же урок, что и в самой калибровке: не доверять новой проверке без
проверки на реальных данных. У нас уже есть готовый датасет — все 24
(кроп, ответ) из раунда 1+2 калибровки с уже известной ручной разметкой
(0 ошибок Gemini, 2 Claude, 5 GPT-4o-mini, все зафиксированы в
`vlm-model-calibration-2026-08-05.md`). Прогнать `judge_defects()`
ретроспективно на этих 24 связках (без повторной генерации описания —
только сам judge-вызов), сверить: ловит ли все 7 известных ошибок
(recall самого judge), не флагает ли ложно ни один из 17 чистых ответов
(false-positive rate). Промах — повод править формулировку вопросов ДО
мержа. Бюджет: ~24 доп. вызова того же порядка цены (~$0.15-0.25) —
отдельное согласование живого прогона перед его фактическим запуском
(эта спека фиксирует план, не тратит деньги сама по себе).

## Тестовое покрытие

- `tests/unit/test_vlm.py`: `judge_defects()` — мокнутый `VlmClient.send()`,
  все комбинации ответа (чистый/hallucination/mermaid_fit=no/language=no/
  нераспознанный формат) → верные строки-дефекты или warning.
- `tests/unit/test_vlm.py`: `enhance_docx_markdown` — `vlm_verify=False`
  (дефолт) никогда не вызывает judge (тот же приём «без сети», что и
  существующий офлайн-тест); `vlm_verify=True` на cache-miss вызывает
  judge ровно раз на маркер; частичный cache-hit (кэш без verdict +
  `vlm_verify=True`) вызывает ТОЛЬКО judge, не переgenerates описание,
  дописывает `judge_verdict` в кэш.
- Живая валидация (§5) — отдельный коммит, результат документируется
  рядом с `vlm-model-calibration-2026-08-05.md`.

## План коммитов/PR

1. `docs: draft spec for witness-gate-redesign`
2. `feat: add Config.vlm_verify flag`
3. `feat: refigure/vlm.py — judge_defects() discriminative judge`
4. `feat: wire judge_defects into enhance_docx_markdown + cache upgrade path`
5. `docs: witness_defects/vlm_witness_min_recall — document language-blindness`
6. `test: tests/unit/test_vlm.py — judge_defects unit coverage`
7. `test: tests/unit/test_vlm.py — vlm_verify wiring + partial cache-hit`
8. `chore: validate judge_defects against the 24 labeled calibration responses`

## Чек-лист реализации

- [ ] `Config.vlm_verify` добавлен
- [ ] `judge_defects()` реализован
- [ ] вписан в `enhance_docx_markdown` + апгрейд частичного кэша
- [ ] докстроки обновлены (языковое ограничение)
- [ ] юнит-тесты `judge_defects`
- [ ] юнит-тесты `vlm_verify`-проводки + частичный cache-hit
- [ ] валидация на 24 размеченных ответах проведена, результат задокументирован

## Вне скоупа

- Embedding-метрики (BERTScore/multilingual CLIPScore) для бесплатного
  пути — тяжёлая ML-зависимость, явное решение пользователя 2026-08-05.
- Claim-decomposition (RAGAS-style) — больше движущихся частей, не
  оправдано на масштабе refigure, явное решение пользователя.
- Self-consistency sampling (SelfCheckGPT) — не ловит систематические
  (не случайные) ошибки вроде 8/8 GPT-4o-mini, явное решение пользователя.
- `.xlsx` — VLM-пути нет вообще, неприменимо.
- `vlm_verify=True` по умолчанию — остаётся opt-in, как и сам `use_vlm`
  (не анонсирован в v1).

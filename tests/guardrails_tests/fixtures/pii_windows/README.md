# pii_windows

Корпус для регрессии окон анализа PII. **Значения в файлах синтетические**: ФИО, email, телефоны, ИНН и СНИЛС выдуманы,
домены — `example.*`. Формы повторяют длинные сообщения, которые уходят в гардрейл: однострочный JSON результата инструмента, прайс-таблица,
код, сообщение пользователя в сотни строк.

Читай файлы так: `open(path, encoding="utf-8", newline="")`. Без `newline=""` CRLF превращается в LF и оффсеты съезжают. Оффсеты — в символах.

## manifest.json

Запись на файл: `file`, `kind` (`synthetic` — собран генератором под шов, `rewritten` — форма с реального сообщения, значения заменены),
`form`, `chars`, `newlines`, `planted` (число записей в `expected_entities.json`), `windows`, `seams`.

`windows` — окна, как их выдаёт `plan_windows` на этом тексте: `start`, `end`, `own_lo`, `own_hi` (зона владения). У файлов с раскодированием (`decoded_chars` — длина раскодированного текста) окна
считаются по раскодированному тексту, как делает гардрейл.

`seams` — какая сущность на каком шве. `window` — индекс окна из `windows`, `entity` — индекс записи в `expected_entities.json` этого файла.

| role | где лежит сущность или пара символов |
|---|---|
| `window_end_split` | разрез окна `window` проходит внутри сущности |
| `next_window_start` | сущность начинается ровно с начала окна `window` |
| `zone_boundary_last_owned` | граница зоны `own_hi` окна `window` проходит внутри сущности |
| `zone_boundary_first_owned` | сущность начинается ровно с `own_hi` окна `window` |
| `hard_cut_split` | жёсткий разрез (блоб без разделителей) проходит внутри сущности |
| `hard_start_inside_entity` | жёсткое начало окна `window` попадает внутрь сущности |
| `address_split_by_newline` | адрес переносится строкой, разрез окна стоит на этом переносе |
| `crlf_pair_straddles_limit` | `\r\n` стоит на `at`, предельная длина окна пришлась между `\r` и `\n` |
| `cut_after_crlf` | окно заканчивается сразу после `\r\n` на `at` |
| `entity_before_cut`, `entity_after_cut` | сущность упирается в разрез слева или справа |

## expected_entities.json

Посаженные сущности по файлам, по возрастанию `start`: `entity_type`, `start`, `end`, `text`, `detector`.
Список неполон: в `rewritten`-файлах есть и другие сущности, которых здесь нет. Эталон для правил — полный прогон `PiiRuleEngine` с боевым рулбуком,
эталон для анализатора — полный разбор без окон.

`detector: "rules"` — запись находится движком правил на этих оффсетах с этим типом (проверяет тест).
`detector: "analyzer"` — запись находит анализатор; `ru2_detects_isolated` говорит, нашёл ли её `presidio-analyzer:2.2.362-ru2` на срезе ±500 символов вокруг.

Для файлов, где срабатывает раскодирование (`seam_json_u_escapes_28k.json`), `start`/`end` — оффсеты в сыром тексте с `\uXXXX`, `decoded_start`/`decoded_end` — в раскодированном; окна и швы заданы
в раскодированных, `text` — раскодированное значение.

## Проверка

`tests/guardrails_tests/test_pii_windows_corpus.py` сверяет оффсеты с текстом, длины с manifest, `windows` с `plan_windows` и швы с окнами.
Текст для окон и оффсетов берётся так же, как в гардрейле: если `decode_json_escapes` срабатывает, это раскодированный текст (в manifest у такого файла есть `decoded_chars`).
`rulebook.yaml` — рулбук для сверки `detector: "rules"` с `PiiRuleEngine`.

```bash
pytest tests/guardrails_tests/test_pii_windows_corpus.py
```

Изменился `plan_windows` — перегенерируй `windows` и швы: тест падает.

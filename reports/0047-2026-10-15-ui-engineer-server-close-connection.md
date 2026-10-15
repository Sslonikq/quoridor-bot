# 0047 · Анализатор: закрытие соединения при раннем ответе, обрывы в GET

- **Дата:** 2026-10-15
- **Агент:** ui-engineer
- **Статус:** done
- **Связанные отчёты:** 0046 (пп. 1 и 2 «Найденных проблем»), 0045

## Задача
Два мелких бага `server.py` из отчёта 0046.

## Что сделано
- `_post`: на ранних выходах без вычитанного тела (403, 404, 413) `self.close_connection = True`; `_send` в этом случае добавляет `Connection: close`. Для 413 с телом до 1 МБ тело дополнительно вычитывается и отбрасывается: иначе на Windows закрытие с непрочитанными данными даёт RST, и клиент иногда теряет сам ответ 413 (поймано флаки-прогоном теста).
- `do_GET` разделён на обёртку и `_get`; обёртка глотает `BrokenPipeError/ConnectionResetError/ConnectionAbortedError`, как `do_POST`.
- Дополнительно (нашлось при прогоне): `Handler.handle` глотает те же ошибки; клиент, оборвавший keep-alive между запросами, давал трейс в `readline`.
- Тесты в `tests/analyzer/test_server.py`: `test_413_closes_connection_without_a_trace` (статус 413, `Connection: close`, `handle_error` не вызывался), `test_get_survives_a_dropped_client` (`_send` бросает BrokenPipe, `handle_error` не вызывался).

## Изменённые файлы
| Файл | Что изменилось |
|---|---|
| `quoridor/analyzer/server.py` | см. выше |
| `tests/analyzer/test_server.py` | +2 теста |

## Проверка
- `pytest tests/analyzer -q -m "not slow"` -> 55 passed, трейсов в stderr нет.
- `ruff check .` ok, `ruff format --check .` ok.

## Breaking changes
Нет.

## Git (выполняет пользователь)
```
git add quoridor/analyzer/server.py tests/analyzer/test_server.py reports/0047-2026-10-15-ui-engineer-server-close-connection.md
git commit -m "fix(analyzer): закрывать соединение после раннего ответа, обрывы в GET" -m "- 403/404/413 без чтения тела: close_connection и Connection: close" -m "- 413: вычитывать тело до 1 МБ, чтобы RST не терял ответ" -m "- do_GET и handle глотают BrokenPipe/ConnectionReset как do_POST" -m "- 2 теста: нет трейса при 413 и обрыве GET"
```

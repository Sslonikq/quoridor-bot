# 0049 · Закрытие замечаний ревью 0048 (анализатор, фаза 7)

- **Дата:** 2026-10-15
- **Агент:** ui-engineer
- **Статус:** done
- **Связанные отчёты:** 0045, 0047, 0048

## Задача
Закрыть замечания ревью 0048: major (нотация в `app.js`), minor 1–3 и дешёвые nit (заголовки безопасности, timeout, флаг mate). Клиент должен стать чисто отрисовочным.

## Что сделано
- **Major.** Сервер отдаёт геометрию вместе с именем. `engine.move_json(move)`: пешка `{name, k:"p", c, r}`, стенка `{name, k:"w", x, y, o}` (внутренние координаты `state.py`), имя из `notation.format_move`. `position.pawns`, `position.walls`, `position.legal`, элементы `top` и новое `position.last` (последний ход) используют этот формат.
- В `app.js` удалены `cellOf`, `wallOf`, `fmtCell`, `fmtWall`, `fmtMove`, `moveOf` и признаки по длине строки. Остался `keyOf(move)` (ключ геометрии `p:c:r` / `w:x:y:o`). Легальные ходы лежат в `Map`, ход пользователя ищется по ключу и в партию пишется `mv.name` с сервера. Клик по строке подсказки передаёт объект из `ana.top`. Подписи колонок `a–i` на краях доски оставлены (это подписи, не нотация).
- Побочное изменение UI: имя нелегальной стенки при наведении не показывается (клиент его не знает), вместо него «✕»; тост «Здесь стенку поставить нельзя».
- **Minor 1.** `json.loads` ловит `RecursionError` вместе с `ValueError`, ответ 400. Тест на 60 000 `[`.
- **Minor 2.** Откат хода в `refresh()` только при `bad_move != null` (`line` обрезается до `bad_move - 1`), иначе тост и остановка без повторного запроса.
- **Minor 3.** Глубина считается от корня: `rank_moves` возвращает `depth + 1` (победа на месте: 1). Описано в докстрингах `rank_moves` и `analyze`; в UI «Глубина N (от корня)».
- **Nit.** Заголовки `X-Content-Type-Options: nosniff` и `Content-Security-Policy: default-src 'self'; frame-ancestors 'none'` (inline-стилей и скриптов в `index.html` нет, проверено). `Handler.timeout = 10`. Флаг `mate` в `top[i]` и `eval_mate` в ответе; константа `WINSCORE` в клиенте удалена.
- Попутно: ранние отказы POST (403/404) теперь вычитывают небольшое тело (`_drain`) перед закрытием соединения. В полном прогоне один раз упал `test_hostile_origin_and_host_rejected` (WinError 10053, сброс соединения до чтения 403), после правки не повторялся.

## Изменённые файлы
| Файл | Что изменилось |
|---|---|
| `quoridor/analyzer/engine.py` | `move_json`, `describe(state, moves)` с геометрией и `last`; `top` с геометрией и `mate`; `eval_mate`; глубина от корня |
| `quoridor/analyzer/server.py` | `RecursionError` -> 400, заголовки безопасности, `timeout`, `_drain` |
| `quoridor/analyzer/static/app.js` | без разбора и форматирования нотации; поиск хода по геометрии; откат по `bad_move`; `mate` с сервера |
| `tests/analyzer/test_engine.py` | геометрия == `parse_move(name)`: легальные ходы, стенки, пешки и `last` в 7 позициях game01, все 128 якорей, `top`; глубина и mate |
| `tests/analyzer/test_server.py` | новый формат `pawns`; тесты: вложенный JSON, заголовки, timeout |
| `tests/analyzer/test_process.py` | новый формат `pawns` |
| `reports/0049-2026-10-15-ui-engineer-phase7-review-fixes.md` | этот отчёт |

## Проверка
- Тесты: `pytest -q -m "not slow"` → 499 passed, 63 deselected, 2 xfailed (один раз до `_drain` падал flaky-тест сброса соединения, см. выше; после правки зелёный). `pytest -q -m slow tests/analyzer` → 4 passed.
- Линт: `ruff check .` → ok; `ruff format` применён.
- `node --check app.js` → ok. Ручной прогон в браузере из этой сессии не выполнялся (нет браузера в окружении): отрисовка проверена только чтением кода и серверными тестами. Нужна проверка пользователем: ход, стенка, переворот, листание `Backspace`, клик по подсказке.
- Перфоманс и сила бота не менялись.

## Решения и почему
- Геометрия стенки в API внутренняя (`x, y` из `state.py`), а не якорь: клиент рисует по ней без пересчёта, а соответствие `parse_move` закреплено тестом.
- Имена нелегальных стенок не отдаются (они нужны только для подписи при наведении); отдавать 128 имён ради подписи не стали.
- `mate` вычисляется сервером через `search.is_win_score`, клиент не знает констант оценки.
- Nit 2 (отмена по `/api/cancel`), 5 (`search_root`), 6, 8, 9 не трогал: вне просьбы.

## Breaking changes
Формат JSON `/api/state` и `/api/analyze`: `pawns`, `walls`, `legal` теперь списки объектов (не строк); в `top[i]` поле `move` заменено на `name` (плюс геометрия и `mate`); `depth` на 1 больше. Внешних потребителей нет, кроме `static/`.

## Проблемы и открытые вопросы
- Ручная проверка UI пользователем (см. выше).
- Остаются nit 2, 5, 6, 8 из 0048.

## Git (выполняет пользователь)
```
git add quoridor/analyzer/engine.py quoridor/analyzer/server.py quoridor/analyzer/static/app.js tests/analyzer/test_engine.py tests/analyzer/test_server.py tests/analyzer/test_process.py reports/0049-2026-10-15-ui-engineer-phase7-review-fixes.md
git commit -m "fix(analyzer): геометрия ходов с сервера, замечания ревью 0048" -m "- app.js не разбирает нотацию: сервер отдаёт геометрию вместе с именем, тест на совпадение с parse_move (в т.ч. 128 якорей)" -m "- RecursionError в JSON -> 400; откат хода только при bad_move" -m "- глубина от корня, флаг mate с сервера" -m "- nosniff, CSP frame-ancestors, timeout у Handler, вычитывание тела при раннем отказе"
```

## Следующий шаг
`code-reviewer`: повторное ревью. Пользователь: ручная проверка в браузере (`python -m quoridor.analyzer`). Затем `test-engineer` (тест подсказки ≤ 3 с на позициях середины партии) и фаза 8.

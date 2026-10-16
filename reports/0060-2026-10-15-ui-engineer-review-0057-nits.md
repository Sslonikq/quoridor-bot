# 0060 · Замечания ревью 0057 в анализаторе (cancel, график в ветке, lock, id)

- **Дата:** 2026-10-15
- **Агент:** ui-engineer
- **Статус:** done
- **Связанные отчёты:** 0057, 0055

## Задача
Закрыть замечания 0057 к `app.js` и `server.py` (п.2 первого шага фазы 8). `engine.py` и `search.py` не тронуты (их правит ai-engineer).

## Что сделано
- `app.js`: `refresh()` вызывает `stopAnalysis(!hintOn)`; в `showHint()` ветки «подсказка из разбора» и «идёт разбор» шлют `/api/cancel`, поэтому старый расчёт не конкурирует с разбором.
- `app.js`: клик по графику идёт через `graphGoto(i)` = `goto(min(i, commonLen()))`: в ветке не уводит на ходы ветки.
- `server.py`: `ReviewJob.error` пишется и читается под `self.lock`.
- `server.py`: `get_review` принимает только `int` (не `bool`, не `float`, не строку).
- Тесты: `test_review.py` (id: `true`, `1.0`, `"1"` -> 404; ошибка под lock), новый `test_client_js.py` + `client_harness.js` (app.js в Node с заглушкой DOM и записью fetch; пропускается без node): нет cancel при обычном обновлении, cancel при идущем разборе / подсказке из разбора / выключенной подсказке, клик по графику в ветке остаётся на 2, в основной партии доходит до 3.

## Изменённые файлы
| Файл | Что изменилось |
|---|---|
| `quoridor/analyzer/static/app.js` | cancel в refresh/showHint, graphGoto |
| `quoridor/analyzer/server.py` | error под lock, строгий id |
| `tests/analyzer/test_review.py` | 2 теста |
| `tests/analyzer/test_client_js.py` | 5 тестов клиента |
| `tests/analyzer/client_harness.js` | харнесс Node |

## Проверка
- Тесты: `pytest -q -m "not slow"` -> 577 passed, 2 xfailed, 81 deselected.
- Линт: `ruff check .` ok; `ruff format --check .` ok.
- В браузере не проверял (Node со stub DOM).

## Решения и почему
- Harness вынесен в `.js`, чтобы ruff не ругался на длинные строки и файл читался как JS.
- Тесты клиента пропускаются без node, зависимость не обязательная.
- Падение тестов без правок отдельно не прогонял (git reset запрещён).

## Breaking changes
Нет. `/api/review/status` с нецелым `id` теперь 404 (раньше `1.0`/`true` находили разбор №1).

## Проблемы и открытые вопросы
- Ручная проверка UI в браузере за пользователем.

## Git (выполняет пользователь)
```
git add quoridor/analyzer/static/app.js quoridor/analyzer/server.py tests/analyzer/test_review.py tests/analyzer/test_client_js.py tests/analyzer/client_harness.js reports/0060-2026-10-15-ui-engineer-review-0057-nits.md
git commit -m "fix(analyzer): cancel при обновлении, клик по графику в ветке, lock, id" -m "- refresh и showHint шлют /api/cancel, когда нового анализа нет" -m "- клик по графику в ветке не уходит правее общего начала" -m "- ReviewJob.error под lock; get_review принимает только int" -m "- тесты: сервер и клиент в Node" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```
(app.js и server.py содержат также незакоммиченные правки 0054–0056: коммитить после сводных команд из 0057.)

## Следующий шаг
Ручная проверка UI; затем закрытие фазы 8.

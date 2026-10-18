# 0072 · Анализатор: гонка cancel/analyze и живая глубина «Глубокого анализа»

- **Дата:** 2026-10-16
- **Агент:** ui-engineer
- **Статус:** done
- **Связанные отчёты:** 0069 (minor), 0070

## Задача
Первый шаг фазы 9, пункт 1: гонка `/api/cancel` и `/api/analyze` в `startDeep`/`cancelDeep` и живая глубина глубокого анализа.

## Что сделано
- Гонка: `startDeep` и `cancelDeep` вызывают `stopAnalysis(false)`. Новый `/api/analyze` сам отменяет старый на сервере (`start_job`), а отдельный `/api/cancel` мог дойти позже и убить новый расчёт. Без подсказки `cancelDeep` шлёт явный cancel (нового анализа нет).
- `analyze()`: при `res.cancelled` и `my === aseq` (запрос актуален, но убит чужим cancel) один раз повторяется; при повторной отмене состояние сбрасывается в `idle`, интерфейс не висит в «Считаю…».
- Живая глубина: `Searcher.on_depth` (необязательный колбэк, вызывается после каждой завершённой итерации `_search`), `engine.rank_moves/analyze(..., on_depth)`, сервер хранит глубину текущего задания (устаревшее задание её не перезаписывает), новый `POST /api/progress` -> `{ok, busy, depth}`. Клиент опрашивает его раз в 0,5 с, пока идёт глубокий анализ: «Глубокий анализ: N из 20 с, глубина D».

## Изменённые файлы
| Файл | Что изменилось |
|---|---|
| `quoridor/analyzer/static/app.js` | гонка cancel/analyze, повтор при чужой отмене, опрос `/api/progress` |
| `quoridor/analyzer/server.py` | `/api/progress`, `on_depth`/`progress`, передача колбэка в `engine.analyze` |
| `quoridor/analyzer/engine.py` | параметр `on_depth` в `rank_moves` и `analyze` |
| `quoridor/search.py` | только `Searcher.on_depth = None` (+ импорт `Callable`) и вызов в `_search` после итерации (4 строки) |
| `tests/analyzer/client_harness.js`, `test_client_js.py` | сценарии 12-14, обновлены ожидания Esc / второго клика (cancel больше не шлётся) |
| `tests/analyzer/test_server.py` | `/api/progress`, устаревшее задание; fake `analyze` принимает `on_depth` |

## Проверка
- `pytest -q -m "not slow" tests/analyzer` -> 186 passed.
- `pytest -q -m "not slow" tests/search` -> 163 passed, 1 failed: `test_eval_v4.py::test_practical_loss_stays_within_the_slack_and_prefers_lengthening_walls`. Правка `search.py` (колбэк) на `_practical_loss` не влияет; вероятно, это параллельная правка `search.py` другим агентом (`_root_moves` и т.п.). Не проверял на чистом дереве (git запрещён), прошу сверить.
- `ruff check .` -> ok. `ruff format --check` для analyzer и tests/analyzer -> ok; `quoridor/search.py` не отформатирован (`_root_moves`, чужая правка).
- Вручную в браузере не прогонял: проверка через Node-харнесс и реальный сервер в тестах.

## Решения и почему
- Колбэк в `Searcher` атрибутом, а не параметром конструктора: не трогает сигнатуры и `bots.py`.
- Глубина берётся из основного поиска `search_top` (то же значение, что станет `depth` результата).
- Повтор один раз, а не цикл: защита от бесконечных запросов, если сервер отменяет по другой причине.

## Breaking changes
Нет (новый маршрут и необязательные параметры).

## Проблемы и открытые вопросы
- `busy` в `/api/progress` остаётся true после завершения анализа до следующего cancel/job; клиент его не использует.
- Падающий тест `test_practical_loss...` см. выше.

## Git (выполняет пользователь)
```
git add quoridor/analyzer/static/app.js quoridor/analyzer/server.py quoridor/analyzer/engine.py quoridor/search.py tests/analyzer/client_harness.js tests/analyzer/test_client_js.py tests/analyzer/test_server.py reports/0072-2026-10-16-ui-engineer-deep-race-live-depth.md
git commit -m "fix(analyzer): гонка cancel/analyze и живая глубина глубокого анализа" -m "- startDeep/cancelDeep без racing /api/cancel, повтор при чужой отмене" -m "- Searcher.on_depth и POST /api/progress, опрос из клиента" -m "- тесты сервера и клиента"
```
Внимание: `quoridor/search.py` содержит и чужие правки; при необходимости добавлять через `git add -p`.

## Следующий шаг
Пункты 2-3 первого шага фазы 9 (test-engineer, ai-engineer).

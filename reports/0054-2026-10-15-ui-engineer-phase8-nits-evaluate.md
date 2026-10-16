# 0054 · Nit фазы 7 в клиенте, /api/cancel, /api/evaluate

- **Дата:** 2026-10-15
- **Агент:** ui-engineer
- **Статус:** done (UI в браузере не открывался: проверено сервером, тестами и `node --check`)
- **Связанные отчёты:** 0048, 0051, 0053

## Задача
Первый шаг фазы 8, п. 1 (ROADMAP) для ui-engineer, плюс маршрут `/api/evaluate` поверх `engine.evaluate_move`.

## Что сделано
- `static/app.js`:
  - `ANALYZE_SECONDS = 3`; в подсказке у каждого хода «· гл. N», у альтернатив с `exact=false` оценка со знаком «≤» (для доказанного мата знак не ставится); строка под списком: «Глубина N, T с … «≤» — оценка не лучше указанной».
  - Откат неподтверждённого хода: `okLine` хранит последний подтверждённый сервером список; при ошибке без `bad_move` и при отсутствии связи `line = okLine` (работает и для undo, и для «Новая»).
  - Два счётчика: `seq` только для `/api/state`, `aseq` для `/api/analyze`. Выключение подсказки не отбрасывает ответ состояния.
  - `stopAnalysis(cancel)`: при скрытии подсказки (H) отменяет fetch и шлёт `/api/cancel`.
  - Подсветка последней стенки: контур вокруг `wallRect(pos.last)` (геометрия с сервера).
- `server.py`: `POST /api/cancel` (`AnalyzerServer.cancel_job`, всегда `{"ok": true}`, ответ старого анализа `cancelled`); `POST /api/evaluate {"moves","move","time"}` -> `engine.evaluate_move` (поля: move_json + score, mate, depth, why, elapsed, cancelled). Валидация до отмены текущего расчёта: `replay(moves + [move])`; нелегальный/не строка/партия окончена -> 400 `bad_move = len(moves)+1`; ошибка в истории -> её номер. Evaluate занимает тот же слот job, что и analyze.
- `docs/mockups/analyzer.html`: убраны Google Fonts и названия Onest / JetBrains Mono, шрифты системные.
- Тесты `tests/analyzer/test_server.py`: время подсказки на game01 n=3 и 18 (slow, 8 passed); `test_cancel_stops_the_running_analysis`; `test_evaluate_scores_the_given_move`; `test_evaluate_rejects_bad_input` (6 случаев); `test_rejected_evaluate_does_not_cancel_the_running_analysis`.

## Изменённые файлы
| Файл | Что изменилось |
|---|---|
| `quoridor/analyzer/static/app.js` | см. выше |
| `quoridor/analyzer/server.py` | /api/cancel, /api/evaluate, docstring |
| `docs/mockups/analyzer.html` | без Google Fonts |
| `tests/analyzer/test_server.py` | новые тесты, n=3 и 18 |

## Проверка
- `pytest -q -m "not slow"`: 527 passed, 2 xfailed. Slow `-k game01_positions`: 8 passed.
- `ruff check .` ok, `ruff format .` без изменений.
- Ручной прогон в браузере не делался (нет доступа к окну): пользователю проверить ход, стенку, переворот, H, подсветку последней стенки.

## Решения и почему
- Откат через `okLine`, а не через pop: единый путь для play/undo/new.
- Evaluate использует общий слот job: разбор партии идёт последовательно, а новый анализ должен прерывать оценку.

## Breaking changes
Нет (маршруты добавлены).

## Проблемы и открытые вопросы
- Все изменённые файлы пользователя в рабочей копии (search.py, engine.py, game02.txt, отчёт 0053) в этот шаг не входят.

## Git (выполняет пользователь)
```
git add quoridor/analyzer/static/app.js quoridor/analyzer/server.py docs/mockups/analyzer.html tests/analyzer/test_server.py reports/0054-2026-10-15-ui-engineer-phase8-nits-evaluate.md
git commit -m "feat(analyzer): /api/evaluate, /api/cancel, nit клиента фазы 7" -m "- глубина и знак ≤ в подсказке, 3 с по умолчанию" -m "- откат неподтверждённого хода, отдельный счётчик анализа, подсветка последней стенки" -m "- макет без Google Fonts; тесты cancel/evaluate и время на game01 n=3, 18"
```

## Следующий шаг
Фаза 8: загрузка партии и разбор через `/api/evaluate`; прогресс разбора.

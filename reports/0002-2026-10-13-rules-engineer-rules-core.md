# 0002 · Ядро правил: состояние, ходы, стенки, BFS, нотация

- **Дата:** 2026-10-13
- **Агент:** rules-engineer
- **Статус:** done
- **Связанные отчёты:** 0001

## Задача
Фаза 1 «Правила»: неизменяемое состояние, генерация ходов (шаги, прыжки, диагонали), легальность стенок с проверкой пути обоих игроков, победа и нотация сайта в отдельном модуле.

## Что сделано
- `state.py`: `GameState` (frozen, slots), `PawnMove`, `WallMove`, `initial()`, `blocked()`.
- `paths.py`: BFS `shortest_path_length` / `has_path_to_goal` (фишки не учитываются).
- `rules.py`: `pawn_moves`, `wall_moves`, `legal_moves`, `is_legal_move`, `is_wall_legal`, `apply_move`, `winner`, `is_terminal`.
- `notation.py`: `parse_move` / `format_move`; привязка якоря стенки изолирована в двух функциях и явно помечена как неподтверждённая.
- `pyproject.toml` (pytest, ruff; тесты запускаются из корня через `pythonpath`).
- Тесты: прыжки/диагонали, стенки (границы, перекрытие, крест, T-стыки, полная блокировка любого игрока), счётчики, победа, случайные партии, BFS, нотация.

## Изменённые файлы
| Файл | Что изменилось |
|---|---|
| `pyproject.toml` | создан |
| `quoridor/__init__.py`, `state.py`, `rules.py`, `paths.py`, `notation.py` | созданы |
| `tests/helpers.py` | хелпер `mk` для сборки позиций |
| `tests/rules/test_pawn_moves.py`, `test_walls.py`, `test_game.py` | созданы |
| `tests/paths/test_bfs.py` | создан |
| `tests/notation/test_notation.py` | создан |

## Проверка
- Тесты: `pytest -q` → 74 passed, 0 failed
- Линт: `ruff check .` → ok; `ruff format --check .` → ok
- Перфоманс: `wall_moves` из начальной позиции (128 стенок, 2 BFS на каждую) ≈ 10 мс. Оптимизаций пока нет (наивная версия служит эталоном для будущих инкрементальных проверок).

## Решения и почему
- Внутренние координаты: клетка `(col,row)` 0..8; стенка `(x,y,orient)` 0..7, привязана к правому верхнему углу клетки `(x,y)`: `h` лежит между рядами y и y+1 (колонки x, x+1), `v` между колонками x и x+1 (ряды y, y+1).
- Нотация: допущение «якорь стенки = клетка, у чьего верхнего правого угла стоит стенка» -> `e3h` = `WallMove(4,2,'h')`. НЕ подтверждено; менять только `_anchor_to_internal` / `_internal_to_anchor` в `notation.py`.
- Стенки хранятся двумя `frozenset` (h/v); битборды отложены до фазы скорости.
- `apply_move` проверяет легальность и бросает `ValueError`.
- Python: на машине 3.11.9, поэтому `requires-python >=3.11` (CLAUDE.md говорит 3.12+; 3.12-only синтаксис не использован).
- Выигравшему не даются ходы (`legal_moves` пуст после победы).

## Breaking changes
Нет (первая реализация API).

## Проблемы и открытые вопросы
- Привязка якоря стенки на сайте не подтверждена (см. выше).
- Совпадает ли нумерация с сайтом (ряд 1 у белых, колонка `a` слева), принято по CLAUDE.md.
- Критерий фазы «партия со скриншота проигрывается без ошибок» не проверен: нет скриншота/партии.
- Если пат невозможен (всегда есть шаг при существующем пути), особой обработки нет.
- Python 3.11 vs 3.12+ в CLAUDE.md.

## Git (выполняет пользователь)
```
git add pyproject.toml quoridor/__init__.py quoridor/state.py quoridor/rules.py quoridor/paths.py quoridor/notation.py tests/helpers.py tests/rules/test_pawn_moves.py tests/rules/test_walls.py tests/rules/test_game.py tests/paths/test_bfs.py tests/notation/test_notation.py reports/0002-2026-10-13-rules-engineer-rules-core.md
git commit -m "feat(rules): состояние, ходы, стенки, BFS, нотация" -m "- неизменяемое GameState, шаги, прыжки, диагонали" -m "- стенки: границы, перекрытие, крест, запрет блокировки пути обоих игроков" -m "- notation.py с неподтверждённой привязкой якоря" -m "- 74 теста, pytest и ruff зелёные"
```

## Следующий шаг
`test-engineer`: независимые edge-case тесты и сверка с партией со скриншота; затем `code-reviewer`. После подтверждения нотации сайта обновить `notation.py`. Позже (фаза 5) инкрементальная проверка стенок с тестом на эквивалентность наивной версии.

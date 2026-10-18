# 0071 · Критерии game02/game06 устойчивы к порядку равных ходов

- **Дата:** 2026-10-16
- **Агент:** test-engineer
- **Статус:** done
- **Связанные отчёты:** 0068, 0069

## Задача
Фаза 9, шаг 1, п. 2: критерии «game02 ход 20: f2 не топ-1» и «game06 18...: не стенка» зависели от порядка равных ходов (h5v/f2/f2h +6.25; f4/b5h -205 при 5 с). Убрать дубль xfail «phase 10».

## Что сделано
- `test_engine.py::test_game02_move_20_top1_is_not_f2_and_f2_is_not_better_than_the_wall`: вместо `top[0] != f2` хелпер `assert_not_better_than_others` (какой-то другой ход в топе не хуже f2 по оценке).
- `test_phase8_v4.py::test_game02_move_20_top1_is_not_f2`: то же условие.
- `test_games_04_06.py::test_game06_analyzer_keeps_the_last_walls`: вместо `top[0].k == "p"` — в топе есть фишечный ход с оценкой не хуже топ-1 (чёрные: меньше = лучше). Стенка впереди допустима только при равенстве с ходом фишкой.
- Удалён дублирующий xfail `test_game02_move_20_f2_is_not_in_the_top_known_limit` из `test_engine.py` (остаётся xfail «f2 не в топ-2» в `test_phase8_v4.py`).
- Код `quoridor/` не менялся.

## Изменённые файлы
| Файл | Что изменилось |
|---|---|
| `tests/analyzer/test_engine.py` | хелпер, ослаблен критерий, удалён дубль xfail |
| `tests/analyzer/test_phase8_v4.py` | критерий f2 по оценке |
| `tests/analyzer/test_games_04_06.py` | критерий game06 по оценке |

## Проверка
- Целевые тесты (game02 ход 20, last_walls, moves_19_20): 8 passed, 1 xfailed.
- Полный `pytest -q` не гонялся (параллельные правки других агентов).
- `ruff check tests`: одно замечание E501 в `tests/analyzer/test_client_js.py:86` (чужой файл, ui-engineer); мои файлы чистые.
- Замеры 5 с: game06 n=35 `f4 -205, b5h -205, g5 -105`; game02 `h5v/f2/f2h 625`, глубина 16.

## Решения и почему
Сравнение по оценке, а не по имени/порядку: ослабляет проверку только на равных ходах, при строгом преимуществе стенки/f2 тест падает.

## Breaking changes
нет

## Проблемы и открытые вопросы
- Тест game06 использует 3 с, а не DEFAULT_TIME (5 с); при 3 с не перепроверен отдельно по значениям, проходит.

## Git (выполняет пользователь)
```
git add tests/analyzer/test_engine.py tests/analyzer/test_phase8_v4.py tests/analyzer/test_games_04_06.py reports/0071-2026-10-16-test-engineer-tie-robust-criteria.md
git commit -m "test(analyzer): критерии game02/game06 устойчивы к равным ходам" -m "- сравнение по оценке вместо порядка равных ходов" -m "- удалён дубль xfail phase 10 в test_engine.py"
```

## Следующий шаг
ai-engineer: п. 3 первого шага фазы 9.

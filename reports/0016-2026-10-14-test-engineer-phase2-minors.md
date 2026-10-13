# 0016 · Фаза 3, шаг 1: minor'ы ревью 0013 в тестах и маркер slow

- **Дата:** 2026-10-14
- **Агент:** test-engineer
- **Статус:** done
- **Связанные отчёты:** 0013, 0015

## Задача
Закрыть minor 3, 4, 5 и nit 8 из ревью 0013 только на уровне тестов (код в `quoridor/` не менялся).

## Что сделано
- minor 3: `test_overlapping_wall_rejected` теперь проверяет `count("нелегален") == 2` и ровно один "Бот ходит". Повтор `e3h` и крест `e3v` реально отклоняются.
- minor 4: полнота `candidate_walls` проверяется на 6 seed'ах random_game (параметризация, каждая 4-я позиция). Проверяется: все кандидаты легальны и без дублей, множество стенок, удлиняющих путь соперника, входит в кандидатов. Добавлен кейс `walls_left == 0` для стороны на ходу (оба цвета): пустой список, при 10 стенках список непустой.
- minor 5: `test_render_junction_matches_wall_sets_on_random_positions` сверяет символ стыка с `h_walls`/`v_walls` (`-`, `|` или пробел) на 6 seed'ах с wall_bias=0.8, с проверкой, что встретились и горизонтальные, и вертикальные стенки (>20 каждых).
- nit 8: маркер `slow` зарегистрирован в `pyproject.toml`. Помечены 10 самых долгих тестов: 5 hypothesis-тестов в `test_properties.py`, `test_random_playouts_stay_consistent`, `test_greedy_beats_random`, `test_greedy_chooses_wall_only_if_strictly_better`, `test_moves_always_legal_on_random_positions`, `test_unchecked_equals_checked_on_legal_moves`.
- Багов не найдено. Падающих тестов нет.

## Изменённые файлы
| Файл | Что изменилось |
|---|---|
| `tests/cli/test_cli_e2e.py` | точная проверка == 2 (minor 3) |
| `tests/bots/test_bots.py` | параметризованная полнота candidate_walls, walls_left == 0, маркеры slow |
| `tests/cli/test_cli.py` | тест стыков по h_walls/v_walls |
| `tests/rules/test_properties.py`, `tests/rules/test_game.py`, `tests/rules/test_apply_unchecked.py` | `@pytest.mark.slow` (в последнем добавлен import pytest) |
| `pyproject.toml` | регистрация маркера `slow` |

## Проверка
- `pytest -q -m "not slow"` -> 219 passed, 10 deselected, 17.0 с
- `pytest -q` -> 229 passed, 58.4 с (до правок: 220 passed, 44 с; разброс по времени из-за нагрузки машины, новые тесты добавляют около 3-4 с)
- `ruff check .` -> ok, `ruff format .` применён

## Решения и почему
- Маркировали по `pytest --durations`, а не целыми файлами, чтобы быстрый режим сохранил максимум проверок (правила, нотация, CLI).
- Описание маркера на ASCII: pyproject.toml читается pytest/ruff как UTF-8, чтобы не рисковать кодировкой.

## Breaking changes
нет

## Проблемы и открытые вопросы
- Рекомендуется добавить в CLAUDE.md строку про `pytest -q -m "not slow"` (по заданию я его не трогал).

## Как запускать
- Быстро (для цикла разработки): `pytest -q -m "not slow"`
- Полностью (перед коммитом/ревью): `pytest -q`
- Только медленные: `pytest -q -m slow`

## Git (выполняет пользователь)
```
git add tests/cli/test_cli_e2e.py tests/bots/test_bots.py tests/rules/test_properties.py tests/rules/test_game.py pyproject.toml reports/0016-2026-10-14-test-engineer-phase2-minors.md
git commit -m "test(cli): закрыты minor'ы ревью фазы 2, маркер slow" -m "- точная проверка двух отказов для повтора и креста стенки" -m "- полнота candidate_walls на случайных позициях и walls_left == 0" -m "- символы стыков рендера сверяются с h_walls/v_walls" -m "- маркер slow: pytest -m \"not slow\" около 17 с"
```
Примечание: `tests/cli/test_cli.py` (тест стыков) и `tests/rules/test_apply_unchecked.py` (маркер slow) уже попали в коммит 839f5bd, поскольку были изменены до него; в этот коммит они не входят.

## Следующий шаг
code-reviewer по шагу, затем ai-engineer: оценка и поиск (фаза 3).

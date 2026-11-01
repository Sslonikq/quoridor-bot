# 0090 · Закрытие замечаний ревью фазы 9 (0088)

- **Дата:** 2026-11-01
- **Агент:** test-engineer
- **Статус:** done
- **Связанные отчёты:** 0087, 0088, 0089

## Задача
Закрыть major 1 (отчёт о матче, см. 0089), minor 5 (slow-тест mcts3 на наборе реальных позиций) и nit 9 (название теста).

## Что сделано
- Отчёт о матче: 0089.
- `test_mcts3_handles_80_percent_of_the_real_positions` (slow): весь набор (21 позиция), `mcts3`, `--budget sims=100000`, порог 80%. Подбор бюджета (два прогона на каждый, результаты детерминированы): 1000 симуляций 52%, 4000 62%, 20000 76%, 100000 81% (17/21; не проходят g02-m20, g03-m16, g07-m25, g07-m27). Запас в одну позицию, это отмечено в docstring. Результат пишется в `arena_results/pytest-mcts3-sims100000.json`.
- Nit 9: `test_analyzer_cancel_before_the_solver_finishes_keeps_working` переименован в `test_analyzer_cancel_in_a_ten_wall_position_keeps_working` (в позиции по 10 стенок решатель не запускается).

## Изменённые файлы
| Файл | Что изменилось |
|---|---|
| `tests/positions/test_real_positions.py` | новый slow-тест mcts3 |
| `tests/search/test_phase9_close.py` | переименован тест |
| `reports/0089-...md`, `reports/0090-...md` | отчёты |

## Проверка
- `pytest -q -m "not slow" tests/positions tests/search/test_phase9_close.py` → 50 passed, 3 deselected.
- Новый slow-тест отдельно (numba) → 1 passed, ~11 с.
- `ruff check .` → ok; `ruff format --check tests` → ok.
- Без numba новый тест не прогонялся (поиск медленнее; детерминизм там не проверен).

## Решения и почему
- 100000 симуляций: минимальный из проверенных бюджетов с результатом >= 80%.

## Breaking changes
Нет.

## Проблемы и открытые вопросы
- Запас в одну позицию: любое изменение mcts3 может уронить тест. Это сигнал, а не флак.
- Без numba тест может идти долго; при необходимости ограничить его маркером numba.

## Git (выполняет пользователь)
```
git add reports/0087-2026-11-01-test-engineer-phase9-close.md reports/0088-2026-11-01-code-reviewer-phase9.md reports/0089-2026-11-01-test-engineer-mcts3-vs-v4-10s.md reports/0090-2026-11-01-test-engineer-phase9-review-fixes.md tests/search/test_phase9_close.py tests/positions/test_real_positions.py
git commit -m "test(search): итог матча фазы 9, slow-тест mcts3, правки ревью" -m "- отчёт о матче mcts3 против v4 при 10 с: 111-89, Wilson [48.6, 62.3]" -m "- slow-тест mcts3 на реальных позициях при sims=100000 (81%)" -m "- переименован тест отмены в позиции по 10 стенок"
```

## Следующий шаг
Основная сессия: ROADMAP (критерий фазы 9), CLAUDE.md, уборка `.cache/`; доказательство силы в фазе 11.

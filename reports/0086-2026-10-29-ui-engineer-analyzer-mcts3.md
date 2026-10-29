# 0086 · Анализатор: кнопка «MCTS» считает на mcts3

- **Дата:** 2026-10-29
- **Агент:** ui-engineer
- **Статус:** done
- **Связанные отчёты:** 0084, 0085

## Задача
Кнопка «MCTS» анализатора должна использовать ту же конфигурацию, что бот `mcts3` из реестра.

## Что сделано
- `engine._new_mcts` создаёт `MCTS(MCTS_SEED, MCTS3_SEARCHER, **MCTS3_OPTIONS)`: константы импортируются из `bots.py`, без дублирования (reserve_combo=75, reserve_k=7, reserve_min=3, solver=2).
- Подсказка кнопки: «MCTS (mcts3): ...». Переиспользование дерева и `reused` не менялись.
- Тест `test_mcts_button_uses_mcts3_with_the_endgame_solver` проверяет опции и searcher-настройки.

## Изменённые файлы
| Файл | Что изменилось |
|---|---|
| `quoridor/analyzer/engine.py` | опции mcts3, комментарии |
| `quoridor/analyzer/static/index.html` | подсказка кнопки |
| `tests/analyzer/test_engine.py` | тест mcts3 вместо mcts2 |

## Проверка
- `pytest -q -m "not slow" tests/analyzer` → 212 passed
- `ruff check .` → ok, `ruff format` применён

## Решения и почему
Подпись только в title кнопки: текст кнопки и имя «MCTS» в сообщениях используются клиентскими тестами.

## Breaking changes
нет

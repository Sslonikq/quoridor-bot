# 0030 · Прямая сборка GameState в apply_move_unchecked и _with_wall

- **Дата:** 2026-10-14
- **Агент:** rules-engineer
- **Статус:** done
- **Связанные отчёты:** 0028, 0029

## Задача
Фаза 5: убрать `dataclasses.replace` из горячих `apply_move_unchecked` и `_with_wall` (замер показал ~7% времени поиска), поведение не менять.

## Что сделано
- Сначала тест эквивалентности `tests/rules/test_apply_equivalence.py`: эталон `ref_apply`/`ref_with_wall` (прежняя реализация через `replace`) сравнивается с новой на всех легальных ходах в позициях 8 случайных партий (>500 пар), плюс проверка типов (tuple/frozenset).
- `_with_wall` и `apply_move_unchecked` собирают `GameState(...)` позиционно, без промежуточного состояния и без `list`-копий кортежей. Импорт `replace` из `rules.py` удалён.

## Изменённые файлы
| Файл | Что изменилось |
|---|---|
| `quoridor/rules.py` | прямая сборка `GameState` |
| `tests/rules/test_apply_equivalence.py` | новый тест эквивалентности |

## Проверка
- Тесты: `pytest -q -m "not slow"` -> 359 passed, 2 xfailed, 22 deselected, 0 failed
- Линт: `ruff check .` ok, `ruff format .` без изменений
- Перфоманс (`apply_move_unchecked`, 8712 вызовов из позиций 10 случайных партий, лучший из 7 прогонов): до 2.57 мкс/вызов -> после 0.81 мкс/вызов (~3.2x; абсолютный выигрыш 1.76 мкс, больше заявленного 0.6, так как в замер входят и ходы стенками, и ходы фишкой)

## Решения и почему
- Порядок полей `GameState` (pawns, h_walls, v_walls, walls_left, turn) используется позиционно; при изменении полей в `state.py` тест эквивалентности это поймает.
- Индекс игрока выбирается через `p == 0`, допускается только `turn` in {0, 1} (как и раньше).

## Breaking changes
Нет.

## Проблемы и открытые вопросы
- Нет.

## Git (выполняет пользователь)
```powershell
git add quoridor/rules.py tests/rules/test_apply_equivalence.py reports/0030-2026-10-14-rules-engineer-direct-gamestate-apply.md
git commit -m "perf(rules): прямая сборка GameState вместо dataclasses.replace" -m "- apply_move_unchecked и _with_wall без replace (2.57 -> 0.81 мкс/вызов)" -m "- тест эквивалентности с прежней реализацией на случайных партиях"
```

## Следующий шаг
test-engineer / code-reviewer.

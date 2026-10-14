# 0034 · Docstring is_wall_legal и тест на плотных досках

- **Дата:** 2026-10-14
- **Агент:** rules-engineer
- **Статус:** done
- **Связанные отчёты:** 0032 (ревью, minor 3 и nit 4), 0028

## Задача
Закрыть minor 3 (документировать предусловие публичной `is_wall_legal` и зафиксировать поведение тестом) и nit 4 (эквивалентность `is_wall_legal` наивной версии и оракулу на плотных досках). Логику правил не менять.

## Что сделано
- `quoridor/rules.py`: docstring у `is_wall_legal` с предусловием «у обоих игроков есть путь до цели», описанием поведения на отрезанном состоянии и советом проверять такие состояния `has_path_to_goal`. Код не менялся.
- `tests/rules/test_wall_density.py` (новый):
  - `test_dense_boards_equal_naive_and_oracle` (6 seed): случайные позиции фишек, `walls_left=(99, 99)`, стенки ставятся случайно до насыщения (пока есть хоть одна легальная). На каждом состоянии все 128 стенок сравниваются с `naive_wall_legal` и `ref_wall_legal`. Проверяется, что плотность реальна (не меньше 25 стенок на доске, не меньше 25 состояний на seed).
  - `test_precondition_cut_off_state_is_documented_behaviour`: белые заперты в {d1, e1} (`h={(3,0)}, v={(2,0),(4,0)}`), стенка `WallMove(0, 6, "h")` вдали: `is_wall_legal` даёт `True`, наивная версия `False`. Фиксирует задокументированное поведение.

## Изменённые файлы
| Файл | Что изменилось |
|---|---|
| `quoridor/rules.py` | только docstring `is_wall_legal` |
| `tests/rules/test_wall_density.py` | новые тесты (7 тестов) |
| `reports/0034-2026-10-14-rules-engineer-wall-legal-docs-dense-test.md` | этот отчёт |

## Проверка
- Тесты: `pytest -q -m "not slow"` → 374 passed, 2 xfailed, 28 deselected, 0 failed
- Линт: `ruff check .` → All checks passed; `ruff format .` → без изменений
- Перфоманс не менялся; новые тесты ~2.5 с.

## Решения и почему
- Поведение на отрезанном состоянии не «чиним», а документируем: быстрая проверка (`_cannot_cut`) и есть причина ускорения в 5 раз, а в играх по правилам предусловие всегда выполнено.
- Плотные доски строятся тем же `is_wall_legal`, но каждое состояние затем проверяется независимым оракулом и наивной версией, так что ошибка быстрой версии при построении не скроет расхождение.

## Breaking changes
Нет (только docstring).

## Проблемы и открытые вопросы
- Фаза 7: на границе vision -> `GameState` проверять `has_path_to_goal` для обоих игроков (остаётся за `vision-engineer`).

## Git (выполняет пользователь)
```powershell
git add quoridor/rules.py tests/rules/test_wall_density.py reports/0034-2026-10-14-rules-engineer-wall-legal-docs-dense-test.md
git commit -m "test(rules): плотные доски и предусловие is_wall_legal" -m "- docstring is_wall_legal: предусловие 'у обоих есть путь'" -m "- тест фиксирует поведение на искусственно отрезанном состоянии" -m "- эквивалентность с наивной версией и оракулом на досках до насыщения" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

## Следующий шаг
- `ai-engineer`: оставшиеся замечания ревью 0032 (minor 1, nit 5, 6) в фазе 6.
- `vision-engineer` (фаза 7): валидация путей на входе состояния.

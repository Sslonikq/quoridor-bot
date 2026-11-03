# 0106 · Плашка nowin читала прототип объекта

- **Дата:** 2026-11-03
- **Агент:** ui-engineer
- **Статус:** done
- **Связанные отчёты:** 0104, 0105

## Задача
Исправить minor из 0105: `?nowin=constructor` (или `toString`) подставлял в плашку текст из `Object.prototype` вместо запасного текста.

## Что сделано
- `app.js`: выбор текста плашки через `Object.prototype.hasOwnProperty.call(NOWIN_TEXT, why) ? why : "missing"` (как для MODES/BOTS).
- `tests/analyzer/test_window_js.py`: снят `xfail(strict=True)`, кейс `?nowin=constructor` теперь обычный параметр теста.

## Изменённые файлы
| Файл | Что изменилось |
|---|---|
| `quoridor/analyzer/static/app.js` | проверка собственного ключа в NOWIN_TEXT |
| `tests/analyzer/test_window_js.py` | снят xfail |
| `reports/0106-2026-11-03-ui-engineer-nowin-proto.md` | этот отчёт |

## Проверка
- Тесты: `pytest -q -m "not slow" tests/analyzer` → 333 passed, 0 failed
- Линт: `ruff check .` ok; `ruff format --check .` ok (203 файла)
- Перфоманс: не менялся
- Матч в arena: не требуется (бот не менялся)

## Решения и почему
Тот же приём, что для режимов и ботов: единый способ защиты от ключей прототипа.

## Breaking changes
нет.

## Проблемы и открытые вопросы
- Реальный pywebview по-прежнему не проверен вручную (см. 0100, 0104).

## Git (выполняет пользователь)
```
git add quoridor/analyzer/static/app.js tests/analyzer/test_window_js.py reports/0106-2026-11-03-ui-engineer-nowin-proto.md
git commit -m "fix(analyzer): плашка nowin не читает ключи прототипа" -m "- выбор текста через hasOwnProperty, ?nowin=constructor даёт запасной текст" -m "- снят xfail в test_window_js.py" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

## Следующий шаг
code-reviewer: повторное ревью фазы 12; пользователь: ручная проверка после `pip install -e .[app]`.

# 0027 · Фаза 5, шаг 1: закрытие замечаний ревью 0025

- **Дата:** 2026-10-14
- **Агент:** test-engineer
- **Статус:** done
- **Связанные отчёты:** 0025 (замечания), 0023, 0024

## Задача
Закрыть minor 1 и nit 2, 3, 5 из 0025. `quoridor/search.py` не трогали (nit 4 за ai-engineer).

## Что сделано
- **Minor 1.** Генератор `threat_positions`: `k` из {1, 2} рядов до финиша; при `k = 2` наша фишка стоит на клетке между соперником и финишем (угроза прямого прыжка). Добавлен точечный тест `test_jump_threat_from_two_rows_white_e7_black_e8`: спасающие стенки ровно `d8h, e8h, d9h, e9h`, `candidate_walls` совпадает с ними, глубины 1-3 играют спасающий ход без ложного мата.
- **Nit 2.** Допуск часов в `test_bot_on_game_clock_stays_within_it`: `< 0.05 + 0.02 * res.moves_a`.
- **Nit 3.** `test_partial_result_when_deadline_hits_inside_iteration_2`: детерминированные «часы» подменяют `search.time`, зависят от счётчика узлов (`_CHECK_EVERY = 1` через monkeypatch). Итерация 2 стартует, первый ход корня доискан, затем срабатывает дедлайн. Проверяется `partial is True`, `depth == 1`, ход легален. От реального времени не зависит.
- **Nit 5.** `format_report`: подпись «D» стала «D (лимит ходов)» (общий `_fmt_tally`, три строки). Тест арены обновлён.

## Изменённые файлы
| Файл | Что изменилось |
|---|---|
| `quoridor/arena.py` | подпись `D (лимит ходов)` в `_fmt_tally` |
| `tests/arena/test_arena.py` | ожидаемая строка отчёта |
| `tests/search/test_search.py` | допуск часов, тест `partial` |
| `tests/search/test_search_defence.py` | генератор k in {1, 2}, точечный тест e7/e8 |
| `reports/0027-2026-10-14-test-engineer-phase5-review-nits.md` | этот отчёт |

## Проверка
- Тесты: `pytest -q -m "not slow"` -> 315 passed, 22 deselected, 2 xfailed, 0 failed. `tests/search/test_search_defence.py` целиком -> 15 passed (включая проверки `> 400` позиций и полноты `_saving_walls` на новом генераторе: расхождений нет). Slow-набор целиком не гонялся.
- Линт: `ruff format .` (1 файл переформатирован), `ruff check .` -> All checks passed.

## Решения и почему
- Для `partial` выбраны fake clock + счётчик узлов вместо малого `time_limit`: с реальными часами момент срабатывания дедлайна внутри итерации 2 недетерминирован.
- Порог fake clock = середина между узлами глубин 1 и 2, проверяется `n2 > n1 + 20`.

## Breaking changes
Нет. Меняется только текст отчёта арены (`D` -> `D (лимит ходов)`).

## Проблемы и открытые вопросы
- Если `search.py` изменится (порядок ходов), порог в тесте `partial` может потребовать пересмотра; тест это проверяет утверждением, а не молча.

## Git (выполняет пользователь)
```
git add quoridor/arena.py tests/arena/test_arena.py tests/search/test_search.py tests/search/test_search_defence.py reports/0027-2026-10-14-test-engineer-phase5-review-nits.md
git commit -m "test(search): закрыты замечания ревью фазы 4 (0025)" -m "- генератор угроз: соперник за 1 или 2 ряда, прыжковая угроза, тест e7/e8" -m "- допуск часов в тесте SearchBot(game_time) устойчив к нагрузке" -m "- тест ветки SearchResult.partial на детерминированных часах" -m "- arena: подпись 'D (лимит ходов)' в отчёте, тест обновлён"
```

## Следующий шаг
`ai-engineer`: nit 4 (`op_goal` вынести из цикла в `search.py`), затем задачи фазы 5 по ROADMAP.

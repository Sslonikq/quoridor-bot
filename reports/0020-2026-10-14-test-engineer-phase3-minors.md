# 0020 · Закрытие minor и nit ревью фазы 3

- **Дата:** 2026-10-14
- **Агент:** test-engineer
- **Статус:** done
- **Связанные отчёты:** 0018 (ревью), 0017 (арена), 0016

## Задача
Первый шаг фазы 4: закрыть все minor и nit из 0018 до появления медленных ботов.

## Что сделано
- minor 1: в `test_report_text_and_mirror_warning` сравниваются партии одного цвета (greedy-зеркало, 4 партии: 0 и 2 белыми, 1 и 3 чёрными).
- minor 2: `test_win_on_last_allowed_ply_is_win_not_draw`: runner против idler, `max_moves = plies` даёт победу, `plies - 1` даёт ничью.
- minor 3: `run_match(..., on_game=None)`: колбэк после каждой партии, сам `run_match` без I/O. `main` печатает строку партии с `flush=True` по мере игры, `--quiet` колбэк не передаёт.
- minor 4: новое исключение `GameError` (индекс партии, seed партии, цвет A, цвет упавшей стороны, лог уже сыгранных ходов, исходная причина). `play_game` оборачивает любое `Exception` из `choose_move` и `apply_move` (нелегальный ход, падение бота), принимает новый параметр `seed`. `main` печатает `Ошибка матча: ...` в stderr и возвращает 1, без traceback. Ход добавляется в лог только после успешного применения.
- nit: `reconfigure(utf-8)` для stdout и stderr.
- nit: `DEFAULT_MAX_MOVES`, `BOTS`, `NEAR_DETERMINISTIC`, `BotFactory`, `UnknownBotError`, `make_bot` перенесены в `quoridor/bots.py`. `cli` берёт оттуда лимит и бота (`choices=sorted(BOTS)`, `make_bot`). `arena` импортирует и реэкспортирует те же объекты (тесты не менялись по импортам).
- nit: «партий: N» вместо «N партий»; строка про цвета теперь «Партий: A белыми X, A чёрными Y».
- nit: `KeyboardInterrupt` в `arena.main` даёт код 130 и сообщение «Матч прерван.» в stderr.
- nit: `MatchResult.score` и строка «Счёт A (W + D/2)/n» в отчёте.
- minor про 0016 (PowerShell-кавычки): в git-командах ниже внутри `-m` только одинарные кавычки.

## Изменённые файлы
| Файл | Что изменилось |
|---|---|
| `quoridor/bots.py` | реестр ботов, лимит ходов, `make_bot`, `UnknownBotError` |
| `quoridor/cli.py` | берёт лимит и реестр из `bots` |
| `quoridor/arena.py` | `GameError`, `on_game`, `score`, прогресс из `main`, коды 1 и 130, UTF-8 для stderr, формулировки |
| `tests/arena/test_arena.py` | +12 тестов (колбэк, прогресс и `--quiet`, граница лимита, контекст ошибки в `run_match`/`play_game`/`main`, Ctrl+C, score, формулировки, единый реестр, 2 subprocess slow) и исправленная проверка зеркала |

## Проверка
- Тесты: `pytest -q` → 264 passed, 0 failed (84 с). `pytest -q -m "not slow"` → 249 passed, 15 deselected (31 с).
- Линт: `ruff check .` → All checks passed; `ruff format .` → без изменений.
- Перфоманс: не менялся.
- Матч в arena: сила ботов не менялась. Смоук `--games 4 --a greedy --b random --seed 1` → W 4 / L 0 / D 0, счёт 100%.

## Решения и почему
- `GameError` ловит `Exception` (не `BaseException`), чтобы Ctrl+C шёл отдельным путём (130).
- Исходное исключение сохранено в `cause` и в цепочке `__cause__`, так что при желании отладки из кода traceback доступен.
- Колбэк получает `GameResult`, а не печатает, чтобы `run_match` оставался чистым и тестируемым.
- Реестр вынесен в `bots.py`, а не в новый модуль: там уже живут сами боты, лишний модуль не нужен.

## Breaking changes
Нет. Из `quoridor.arena` по-прежнему импортируются `BOTS`, `make_bot`, `UnknownBotError`, `DEFAULT_MAX_MOVES`. Из `quoridor.cli` по-прежнему `DEFAULT_MAX_MOVES`. Добавлены `GameError`, `on_game`, `score`.

## Проблемы и открытые вопросы
- Нет. Неизменённые nit'ы из 0018 (область коммита 0016, формулировка «фаза 3» в 0016) относились к старому отчёту и не правятся.

## Git (выполняет пользователь)
```
git add quoridor/bots.py quoridor/cli.py quoridor/arena.py tests/arena/test_arena.py reports/0020-2026-10-14-test-engineer-phase3-minors.md
git commit -m "fix(arena): minor и nit ревью фазы 3" -m "- on_game: прогресс по партиям печатается по мере игры, --quiet отключает" -m "- GameError: номер партии, seed, цвет и лог при нелегальном ходе бота, код 1" -m "- реестр ботов и лимит ходов в bots.py, KeyboardInterrupt -> 130, UTF-8 для stderr" -m "- score (W + D/2)/n; тесты границы лимита и исправлена тавтология в зеркале"
```
Если нужен отдельный коммит под рефакторинг реестра: сначала `quoridor/bots.py quoridor/cli.py` с `refactor(bots): реестр и лимит ходов в одном месте`, затем остальное. Но `arena.py` зависит от нового `bots.py`, поэтому проще одним коммитом.

## Следующий шаг
code-reviewer: быстрое ревью этих правок. Затем ai-engineer: оценочная функция и alpha-beta (фаза 4).

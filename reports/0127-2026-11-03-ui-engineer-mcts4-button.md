# 0127 · Кнопка «MCTS4» в анализаторе

- **Дата:** 2026-11-03
- **Агент:** ui-engineer
- **Статус:** done
- **Связанные отчёты:** 0126 (бот mcts4), 0085 (mcts3 в анализаторе)

## Задача
Дать в анализаторе выбор бота `mcts4` (фаза 14) четвёртой кнопкой. По умолчанию остаётся MCTS (mcts3).

## Что сделано
- engine: белый список `BOTS = (v4, v5, mcts, mcts4)`. Таблица `MCTS_BOTS` отображает имя на настройки реестра (`MCTS3_*` / `MCTS4_*` из `quoridor.bots`). `_new_mcts(bot)`, `_rank_mcts(..., bot)`, `rank_moves` и `analyze` берут общий путь для любого бота из `MCTS_BOTS`: время режимов, прогресс, отмена, разбор победы на месте, поля `sims`/`reused`.
- Сохранённое дерево теперь по одному на бота (`_mcts_shared` — словарь). Деревья mcts3 и mcts4 не смешиваются, это важно при переключении кнопки. Блокировка общая. `reset_mcts_tree()` очищает всё, старый API сохранён.
- server: логика не менялась (проверяет по `engine.BOTS`), обновлена только docstring.
- Клиент: `BOTS.mcts4 = {name: "MCTS4"}`, признак `tree` у обоих MCTS (`isTree`), чтобы строки прогресса и результата («главная линия, N симуляций») работали для MCTS4. Кнопка `bot_mcts4` без `full-only`, подпись «MCTS4» (5 символов, в компакте кнопки `flex:1 1 0` с padding 6 px, четыре кнопки умещаются в ~380 px; подпись «M4» не понадобилась), title «MCTS4 (новый, фаза 14)…». Выбор в localStorage тем же ключом; старые значения (`mcts`, `v5`, `v4`) работают, по умолчанию `mcts`.
- Разбор партии (review) на выбранного бота не завязан (v4), не менялся.

## Изменённые файлы
| Файл | Что изменилось |
|---|---|
| `quoridor/analyzer/engine.py` | `MCTS_BOTS`, `mcts4` в `BOTS`, деревья по ботам |
| `quoridor/analyzer/server.py` | docstring |
| `quoridor/analyzer/static/index.html` | кнопка `bot_mcts4`, подпись в шапке |
| `quoridor/analyzer/static/app.js` | `BOTS.mcts4`, `isTree` |
| `tests/analyzer/test_server.py` | `bot=mcts4`: ok, sims, передача в engine |
| `tests/analyzer/test_engine.py` | настройки mcts4, отдельное дерево |
| `tests/analyzer/test_static_compact.py` | `bot_mcts4` не `full-only` |
| `tests/analyzer/client_harness.js`, `test_client_js.py` | отправка, localStorage, строка прогресса MCTS4 |

## Проверка
- `pytest tests/analyzer -q -m "not slow"` → 375 passed, 55 deselected (124 с).
- `ruff check .` → ok; `ruff format .` выполнен.
- Матчей не запускал. Остальные тесты (`tests/search` с `reset_mcts_tree`) целиком не гонял, на их API это не влияет.
- Глазами интерфейс не проверял (нет возможности смотреть окно): ширину компакта оцениваю по CSS, не по скриншоту. Стоит открыть и проверить, что четыре кнопки в одну строку.

## Решения и почему
- Дерево на бота, а не одно общее: иначе при переключении MCTS<->MCTS4 корень «переиспользовался» бы чужим деревом с другими приорами.
- `analyze` по-прежнему возвращает `"bot": "mcts4"`, клиент использует его же.

## Breaking changes
Нет. `engine._new_mcts()` без аргументов по-прежнему mcts3; `_mcts_shared` теперь словарь (тесты его не использовали).

## Проблемы и открытые вопросы
- Подпись в компакте не проверена визуально. Если не влезет, поменять на «M4» только в тексте кнопки, id и ключи не трогать.

## Git (выполняет пользователь)
```
git add quoridor/analyzer/engine.py quoridor/analyzer/server.py quoridor/analyzer/static/index.html quoridor/analyzer/static/app.js tests/analyzer/test_server.py tests/analyzer/test_engine.py tests/analyzer/test_static_compact.py tests/analyzer/client_harness.js tests/analyzer/test_client_js.py reports/0127-2026-11-03-ui-engineer-mcts4-button.md
git commit -m "feat(analyzer): кнопка MCTS4 в выборе бота подсказок" -m "- bot=mcts4 в API: время режимов, прогресс, отмена, переиспользование дерева" -m "- дерево MCTS отдельно для каждого бота" -m "- кнопка в компакте, запоминание в localStorage, тесты сервера и клиента"
```

## Следующий шаг
Визуальная проверка компакта пользователем; сравнение подсказок mcts3/mcts4 на живых позициях.

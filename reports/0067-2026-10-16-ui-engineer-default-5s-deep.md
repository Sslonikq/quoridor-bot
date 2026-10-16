# 0067 · Подсказка 5 с по умолчанию и кнопка «Глубокий анализ» (20 с)

- **Дата:** 2026-10-16
- **Агент:** ui-engineer
- **Статус:** done
- **Связанные отчёты:** 0054, 0060

## Задача
Время подсказки по умолчанию 3 -> 5 с; кнопка «Глубокий анализ» (20 с) для текущей позиции.

## Что сделано
- `engine.py`: только константы: `DEFAULT_TIME = 5.0`, новая `DEEP_TIME = 20.0`, `MAX_TIME = 30.0`.
- `server.py`: общая проверка `_valid_time`: число (не bool), `0 < time <= 30`, NaN/inf/строка/None -> 400 "time must be a number from 0 to 30" для `/api/analyze`, `/api/evaluate`, `/api/review`. Разбор ограничен прежними 10 с на позицию (`REVIEW_MAX_TIME`, как раньше при клампе), чтобы расширение MAX_TIME не удлинило разбор.
- Клиент (`app.js`, `index.html`, `app.css`): `ANALYZE_SECONDS = 5`, `DEEP_SECONDS = 20`; кнопка `#bDeep` в панели над доской; клик запускает `/api/analyze` с `time: 20`, показывает полосу прогресса и "Глубокий анализ: N из 20 с. Esc отменяет."; кнопка превращается в "Отменить". Отмена: повторный клик, Esc, смена позиции/любой новый запрос (`stopAnalysis`), выключение подсказки; после отмены возвращается обычная подсказка 5 с. Результат: "Глубокий, 20 с: глубина N, ...". Интерфейс не блокируется (обычный fetch + AbortController, как у подсказки). Недоступно при выключенной подсказке, окончании партии и идущем разборе (тост).
- Макет `docs/mockups/analyzer.html`: кнопка, прогресс-полоса, демо поведения (20 с, клик/Esc отменяют).
- Внешних ресурсов нет.

## Изменённые файлы
| Файл | Что изменилось |
|---|---|
| `quoridor/analyzer/engine.py` | DEFAULT_TIME 5, DEEP_TIME 20, MAX_TIME 30 |
| `quoridor/analyzer/server.py` | `_valid_time`, REVIEW_MAX_TIME |
| `quoridor/analyzer/static/{app.js,index.html,app.css}` | кнопка, прогресс, отмена, метка результата |
| `docs/mockups/analyzer.html` | кнопка и прогресс |
| `tests/analyzer/test_server.py` | time=20/30 принимается и доходит до engine без клампа; 31, 30.01, 1e9, inf, NaN, 0, -1, bool, строка, None -> 400 (3 маршрута); слой "3 с" -> 5 с |
| `tests/analyzer/test_engine.py` | `test_default_time_is_5s` |
| `tests/analyzer/client_harness.js`, `test_client_js.py` | 7 клиентских тестов в Node (время 5/20, прогресс, отмена сменой позиции/Esc/повторным кликом, метка результата, запреты); фикстура читает вывод Node как UTF-8 |

## Проверка
- `pytest -q -m "not slow"` -> 640 passed, 2 xfailed (один падал из-за cp1251 в фикстуре, исправлено, test_client_js 12 passed).
- `ruff check .` -> ok; `ruff format .` переформатировал 2 файла (мои тесты; search.py/eval.py/bots.py не правил).
- Ручной прогон в браузере не делал (нет GUI у агента): поведение кнопки проверено harness-тестами в Node и серверными тестами; сервер не запускался, процессов не оставлял.

## Решения и почему
- Живой глубины по ходу поиска сервер не отдаёт (`/api/analyze` синхронный, `search.py` трогать нельзя), поэтому прогресс = прошедшее время из 20 с; глубина показывается в результате. Для живой глубины нужен колбэк прогресса в Searcher (задача ai-engineer) и опрос статуса на сервере.
- Ранее time вне [0.1; 10] молча клампился; теперь значения <=0 и >30 дают 400 (явная валидация по заданию).

## Breaking changes
`/api/analyze|evaluate|review`: time <= 0, > 30, NaN/inf теперь 400 вместо клампа.

## Проблемы и открытые вопросы
- Тесты `slow` (default-time 5 с и др.) не гонял.
- В рабочем дереве есть `quoridor/frozen/search_v4.py` (не мой).

## Git (выполняет пользователь)
```
git add quoridor/analyzer/engine.py quoridor/analyzer/server.py quoridor/analyzer/static/app.js quoridor/analyzer/static/index.html quoridor/analyzer/static/app.css docs/mockups/analyzer.html tests/analyzer/test_server.py tests/analyzer/test_engine.py tests/analyzer/client_harness.js tests/analyzer/test_client_js.py reports/0067-2026-10-16-ui-engineer-default-5s-deep.md
git commit -m "feat(analyzer): подсказка 5 с и кнопка «Глубокий анализ» 20 с" -m "- DEFAULT_TIME 5 с, DEEP_TIME 20 с, максимум time 30 с с валидацией (NaN/bool/>30 -> 400)" -m "- кнопка с прогрессом, отмена Esc/кликом/сменой позиции, метка «глубокий, 20 с»" -m "- макет обновлён, тесты сервера и клиента в Node" -m "" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```
(Файлы engine.py, test_server.py, test_engine.py, test_client_js.py, client_harness.js, app.js, index.html, app.css, server.py уже содержат прежние незакоммиченные правки фазы 8; коммитить вместе или разбить по `git add -p`.)

## Следующий шаг
ai-engineer: колбэк прогресса/глубины для живого показа в глубоком анализе (опционально).

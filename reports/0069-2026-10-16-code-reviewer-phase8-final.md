# 0069 · Итоговое ревью фазы 8 «Умный бот v4» (0065–0068)

- **Дата:** 2026-10-16
- **Агент:** code-reviewer
- **Статус:** done
- **Связанные отчёты:** 0064, 0065, 0066, 0067, 0068

## Задача
Ревью всего, что появилось после 0064:
- 0065: перенумерация фаз;
- 0066: `reserve_term`, `quoridor/frozen/search_v4.py`, реестр `bots.py`, `tune --max-gap` и `frozen4:`;
- 0067: анализатор (5 с по умолчанию, «Глубокий анализ» 20 с, `MAX_TIME` 30, валидация `time`);
- 0068: тесты и матч;
- правки CLAUDE.md и ROADMAP.md.

Нужно проверить пересмотренные критерии фазы 8 и решить, можно ли закрывать фазу. Отдельно составить сводный набор git-команд для всех незакоммиченных изменений (0053–0069).

## Что сделано
- Прочитаны CLAUDE.md, ROADMAP.md, отчёты 0064–0068, `git diff` по `eval.py`, `bots.py`, `tests/perf/tune.py`, `server.py`, `app.js` (глубокий анализ), CLAUDE.md, ROADMAP.md. Сравнены `quoridor/frozen/search_v4.py` и `quoridor/search.py`.
- Собственные проверки (скрипты в scratchpad, в репозиторий не добавлялись). Набор: 235 позиций, каждый 3-й полуход game01–06 плюс 6 случайных партий.
  - **v1–v3: HEAD `search.py` против рабочей копии**, глубины 3 и 4, настройки v1, v2 (TT 18), v3 (TT 18, вес 125).
    - Ход и оценка: **0 расхождений**.
    - Число узлов расходится в 4 позициях. Все четыре доказанно проиграны (`is_win_score`, −999996/−999998), ход и оценка совпадают. Это намеренное изменение `_delay_loss` из 0053/0059, как и в 0064.
    - Время одинаковое (v3 d4: 1.4 с против 1.5 с на 235 позиций).
  - **frozen v4 против живого `Searcher` со старыми настройками (`reserve=0`)**, глубины 3 и 4: **0 расхождений** по ходу, оценке и узлам. Время 5.6 с против 5.6 с.
  - Текстовый diff двух модулей. Отличия:
    - в frozen встроены `evaluate_dists`/`evaluate_v4` (байт в байт как в `eval.py`, без `reserve`);
    - в frozen есть `SETTINGS`/`make_searcher`;
    - в живом модуле есть только три строки параметра `reserve`.

    Значит, «старый v4» (рабочая копия до 0066) = frozen. Frozen импортирует только `bitpaths`, `rules`, `state`. От `search.py` и `eval.py` он не зависит, это проверяет тест `test_frozen_v4_imports_only_rules_level_modules`.
  - Контроль, что `reserve=50` действительно меняет игру: 72 из 235 позиций дают другой ход, оценку или число узлов.
  - **game06 17…–20… на новом умолчании 5 с** (`engine.analyze`):

    | ход | топ-3 (оценка, плюс — белым) |
    |---|---|
    | 17… | f5 −205, b5h −155, c4v −155 |
    | 18… | **f4 −205, b5h −205**, g5 |
    | 19… | f4 |
    | 20… | f3, g4, e4 |

    Топ-1 нигде не стенка. Но на 18… f4 и b5h равны (см. minor 2).
- Медленные тесты критериев гонял сам: `test_phase8_v4.py`, `test_games_04_06.py`, `test_eval_reserve.py`, game02-тесты `test_engine.py`. Итог: **59 passed, 8 xfailed, 1 xpassed** (198 с).
- Код не менял.

## Изменённые файлы
| Файл | Что изменилось |
|---|---|
| `reports/0069-2026-10-16-code-reviewer-phase8-final.md` | этот отчёт |

## Проверка
- Тесты: `pytest -q -m "not slow"` → **676 passed, 140 deselected, 2 xfailed** (169 с). Медленные тесты критериев фазы 8: 59 passed, 8 xfailed, 1 xpassed. Полный прогон по 0068: 807 passed, 10 xfailed, 1 xpassed.
- Линт: `ruff check .` → All checks passed; `ruff format --check .` → 145 files already formatted.
- Перфоманс: не менялся (ревью). A/B выше: v1–v3 и frozen по времени без изменений. 0066: 18.0k → 17.7k nodes/s, в пределах шума.
- Матчи: не запускал. Арифметику в отчётах перепроверил, всё сходится:

  | матч | партий | W / L / D | счёт |
  |---|---|---|---|
  | v4 против v4-frozen, 3 с | 100 | 63 / 34 / 3 | 64.5% |
  | v4 против v3, 3 с | 100 | 64 / 36 / 0 | 64.0% |
  | v4 против v4-frozen, 3 с (0068) | 200 | 115 / 82 / 3 | 58.25% |
  | v4 против v4-frozen, 0.2 с | 1600 | 897 / 690 / 13 | 56.5% |

  Вместе с 0068 против v4-frozen при 3 с получается 178/116/6 из 300, 60.3%. Нижние границы интервала Уилсона везде выше 50%.

## Результаты ревью

### Корректность
- **`reserve_term`** (eval.py:60–76) антисимметричен по построению: одна и та же функция `threat` для обеих сторон, член = разность. Целочисленное деление одинаково для обеих сторон, поэтому `reserve_term(a, b) == -reserve_term(b, a)` точно (есть тест). Член добавляется до веток гонки. При `walls_op == 0` и `d_me <= d_op` возвращается `score + RACE` вместе с членом, это согласовано. При `reserve=0` ветка не выполняется: тест `test_reserve_off_is_the_frozen_v4` и мой A/B (0 расхождений) это подтверждают.
- **`bots.py`**:
  - `V4_SETTINGS` = frozen `SETTINGS` + `reserve` (есть тест);
  - `SearchBot(reserve=…)` вместе с `searcher=` запрещён, как и остальные параметры;
  - боты `v4-frozen*` используют `search_v4.make_searcher(seed)` и не зависят от `V4_SETTINGS`;
  - `v3-3s` / `v4-3s` / `v4-frozen-3s` — это `MATCH_MOVE_TIME` = 3.0.
- **`tune --max-gap`**:
  - отметки `time.time()` ставятся до и после каждого хода обоих ботов в одном процессе и очищаются в `on_game`, поэтому промежуток между партиями не учитывается;
  - партия с разрывом больше `gap_limit` не попадает в `kept` и увеличивает `dropped`;
  - `summarize` считает W/L/D, средние времена и полуходы только по оставшимся партиям;
  - `combine` суммирует `dropped` и принимает старые 6-элементные кортежи;
  - Уилсон считается по `n = w + lo + d`, то есть только по оставшимся партиям;
  - тест с поддельными часами проверяет три случая: скачок 100 с → отброшена 1 партия, 20 с → 0, порог 0.5 → все.

  Корректно. Не хватает только проверки соотношения порога и бюджета хода (minor 3).
- **Анализатор**:
  - `_valid_time` (server.py:38–42): bool, строка, None, NaN, inf, ≤ 0 и > 30 дают 400. `10**400` тоже даёт 400: сравнение int с float в Python корректно.
  - Разбор по-прежнему ограничен 10 с на позицию (`REVIEW_MAX_TIME`).
  - Клиент: `ANALYZE_SECONDS = 5`, `DEEP_SECONDS = 20`. Глубокий анализ отменяется повторным кликом, Esc, сменой позиции и выключением подсказки. Есть гонка запросов `/api/cancel` и `/api/analyze` (minor 1).

### Ограничения CLAUDE.md
- Сетевого кода нет. `HOST = "127.0.0.1"`, CSP `default-src 'self'; frame-ancestors 'none'` без изменений. В `static/` и `docs/mockups/analyzer.html` нет внешних URL, кроме XML-namespace SVG.
- `rules.py` и `paths.py` не менялись. `reserve_term` чистая. В новом коде нет нотации: в `eval.py`, `bots.py`, `frozen/search_v4.py`, `tune.py` её нет, в тестах используется `format_move`.
- Горячий путь: `reserve_term` вызывается в каждом листе v4 и при каждом вызове создаёт вложенную функцию (nit 1). Замер 0066 разницы не показал.
- Отчёты 0065–0068 существуют и заполнены по шаблону. Статус 0065 `partial` честный: на тот момент фаза ждала решения.

### Пересмотренные критерии фазы 8 (ROADMAP:15)
| Критерий | Факт | Вывод |
|---|---|---|
| v4 сильнее v3 при равном времени | 0.2 с: 67.5% из 400 (v4 из 0062). Новый v4 при 3 с: 64.0% из 100 против v3 | выполнен |
| п.(5): новый v4 сильнее v3 **и** текущего v4 при 3 с, ≥100 партий → умолчание | 64.0% против v3 [54.2, 72.7], против v4-frozen 64.5% и 58.2% (300 партий в сумме) | выполнен, умолчание сменено обоснованно |
| game02 ход 20: f2 не топ-1 | проходит при 5 с; топ h5v / f2 / f2h, **все +6.25** | формально выполнен, держится на порядке равных ходов (minor 2) |
| game03 ход 16: защитная стенка в топ-3 | проходит (набор ответов на забор, а не любая стенка) | выполнен |
| game03 ход 16: оценка стабильна ±1 шаг между 4 и 20 с | проходит (+4.75 и +4.75); неверность оценки (реальная около −9) честно оставлена xfail и вынесена в бэклог | выполнен |
| game06 17…–20…: не тратить последние стенки | проходит при 3 с (search и анализатор) и при 5 с в моей проверке; на 18… при 5 с f4 = b5h | выполнен, запас тонкий (minor 2); доигрывания 0066 показывают, что 18… b5h ошибкой не является, решает 20… |
| game04/05: ключевые позиции из комментариев | f3v, c7v, g7v (и game06 8… f6) — топ-1 при 3 с на уровне search и анализатора | выполнен |
| анализатор на 3 с достигает глубины SearchBot | тест 0063 проходит | выполнен |

### Ослабленные тесты game02 (0068)
Ослабление обосновано:
- Убранные проверки («топ-1 обязательно стенка», «f2 хотя бы на 100 ниже лучшего») зависели от порядка равных ходов и от шума двух независимых поисков. 0066 показал, что они падают и на старом v4. Значит, это не регрессия `reserve`.
- Смысл ROADMAP сохранён: f2 не топ-1, оценка f2 не лучше топ-1, стенка в топ-3 не хуже лучшего больше чем на шаг.
- Невыполненное («f2 не в топ-2», «оценка < 0», «ходы 11–16 стенка») осталось `xfail(strict=False)` со ссылкой на бэклог.

Подгонки нет. Но оставшийся критерий «f2 не топ-1» держится на том же порядке равных ходов (minor 2).

### Честность отчётов
- Цифры матчей сходятся с W/L/D (см. «Проверка»).
- В 0066 отвергнутый вариант `race_min_gain=1` (45.8%) честно записан как неудачный.
- 0068 честно пишет, что f2 по оценке равен лучшему ходу и что заголовок `tune` печатает «move_time 0.2».
- Игры при 0.2 с (56.5% из 1600) поданы как подбор веса, а не как основание для смены умолчания. Так и должно быть.

## Замечания
- [minor] quoridor/analyzer/static/app.js:183, 191: гонка запросов. `startDeep` и `cancelDeep` вызывают `stopAnalysis(true)`, а он не дожидаясь шлёт `/api/cancel`, и сразу следом уходит `/api/analyze`. Сервер многопоточный (`ThreadingHTTPServer`), порядок обработки двух соединений не гарантирован. Если `cancel_job` выполнится после `start_job` нового запроса, новый анализ получит `cancelled`. Тогда в строке 157 (`if (my !== aseq || res.cancelled) return;`) не вызываются `clearDeep()` и сброс `anaState`. Интерфейс зависает в «Глубокий анализ…» с полной полосой прогресса, после `cancelDeep` — в «Считаю…». Длится это до следующего действия пользователя. `refresh()` этого избегает (`stopAnalysis(!hintOn)`). → `stopAnalysis(false)` в `startDeep`/`cancelDeep`: `start_job` на сервере сам отменяет прежний расчёт. Плюс в `analyze()` при `res.cancelled && my === aseq` выполнять `clearDeep(); anaState = "idle"; render()`. Тест в `client_harness.js` с ответом `cancelled` на активный запрос.
- [minor] tests/analyzer/test_phase8_v4.py:101, tests/analyzer/test_engine.py:270, tests/search/test_eval_reserve.py:124/132: критерии «game02 ход 20: f2 не топ-1» и «game06 18…: не стенка» держатся на порядке равных ходов. h5v и f2 оба +6.25. При 5 с f4 и b5h на 18… оба −205. Это тот же класс нестабильности, из-за которого 0068 ослабил два других теста. Формально критерии выполнены, но тесты могут начать «мигать» при любой правке порядка ходов или скорости машины. → в фазе 9 (глубже поиск) перепроверить. Если появятся ложные падения, переформулировать эти тесты так же, как в 0068: «f2 не лучше топ-1 по отдельной оценке», «стенка не лучше лучшего пешечного хода». Не превращать их в xfail молча.
- [minor] tests/perf/tune.py:179: `--max-gap` не сверяется с бюджетом хода. При `--move-time` ≥ 30 (или `game_time`, где `timeman.MAX_TIME` = 15 с плюс накладные расходы близки к порогу) отбрасываются все или почти все партии. Итог тогда считается по `n=0` или по выборке, смещённой в сторону коротких ходов. → ошибка `argparse`, если `max_gap <= move_time`, либо порог по умолчанию `max(30, 3 * move_time)`.
- [minor] ROADMAP.md: minor и nit из 0064 по-прежнему открыты:
  - `walls_seen` нигде не проверяется (tests/search/test_eval_v4.py:208–232);
  - LMR-тест проверяет только легальность хода (tests/search/test_eval_v4.py:172–177);
  - `search_root(moves=None)` без защитных стенок и потолок при LMR в docstring (search.py:1221);
  - nit про сентинел `MAX_DEPTH * 1000` (search.py:780), аннотацию `set` (search.py:465), обоснование фильтра `_race_cands`, память кеша гонки (теперь при `MAX_TIME` 30 с; потолок 75k записей её ограничивает, но замера нет).

  В ROADMAP нет раздела «Первым шагом фазы 9». → main при закрытии фазы 8 добавляет его с этим списком и пунктами ниже.
- [nit] quoridor/eval.py:71: `reserve_term` при каждом вызове (в каждом листе v4) создаёт вложенную функцию `threat`. → вынести в модуль `_reserve_threat(own, other, reserve)` или считать таблицу `[own][other]` при создании `Searcher`. Влияние по замеру 0066 в пределах шума, но горячие пути не должны аллоцировать.
- [nit] tests/perf/tune.py:209: в итоговой строке печатается `move_time {ns.move_time}` (0.2), даже когда у обоих ботов своё время (`-3s`). → печатать «(по умолчанию для search:/frozen*:)» или не печатать, если обе спецификации из реестра.
- [nit] tests/analyzer/test_engine.py:287: причина xfail «phase 10, v4» устарела после двух перенумераций и дублирует `test_phase8_v4.py:117`. → удалить дубль или сослаться на пункт бэклога, как в `test_phase8_v4.py`.
- [nit] CLAUDE.md:36: в списке `*-0.2s` нет `v4-frozen-0.2s`, хотя бот есть в реестре и строкой выше упомянуты `v4-frozen`, `-clock`, `-3s`. → пользователю (агентам CLAUDE.md править нельзя) дописать `v4-frozen-0.2s`.
- [nit] ROADMAP.md, CLAUDE.md: часть правок сделана после 0065 и ни одним отчётом не описана: новая фаза 9 «Быстрый движок», сдвиг «Разбора партии» на 10, пересмотренные критерии фазы 8, строки `v4-frozen*` и `--max-gap` в CLAUDE.md. Если их делал пользователь, отчёт не нужен. Если main, это описывает закрывающий отчёт main.
- [nit] quoridor/frozen/search_v4.py:5–7: frozen зависит от живого `quoridor.bitpaths`, включая добавленный в фазе 8 `shortest_layers`. Это задокументировано («must not change behaviour anyway»). В фазе 9 (Numba) bitpaths будет переписываться, и тогда A/B «frozen = старый v4» на фиксированной глубине нужно прогнать заново. → test-engineer, фаза 9: оставить `test_frozen_v4_equals_live_search_with_frozen_settings` обязательным и добавить A/B frozen против HEAD до и после переписывания bitpaths.

## Вердикт
**APPROVE WITH NITS.** Blocker и major нет.
- v1–v3 не изменились (A/B с HEAD: 0 расхождений по ходу и оценке).
- Frozen v4 совпадает со старым v4 по ходам, оценкам и узлам и не зависит от `search.py`/`eval.py`.
- При `reserve=0` поведение v4 идентично frozen.
- Офлайн, `127.0.0.1` и CSP соблюдены.
- Пересмотренные критерии фазы 8 и силовой критерий (v4 сильнее v3 и v4-frozen при 3 с, ≥100 партий, нижние границы интервалов выше 50%) выполнены. Отчёты честные.

**Фазу 8 можно закрывать.** Minor 1–4 и nit уходят в «Первым шагом фазы 9».

## Решения и почему
- A/B делал сам, а не по 0064: после 0064 менялись `eval.py`, `search.py` и `bots.py`.
- Узлы в доказанно проигранных позициях считаю допустимым расхождением: ход и оценка совпадают, изменение намеренное (0053/0059) и уже принято в 0057/0064.
- Матчи не перезапускал: цифры 0066 и 0068 согласованы (два независимых прогона против v4-frozen, оба выше 50% нижней границей).
- Гонку cancel/analyze ставлю minor, а не major: зависание редкое, снимается любым действием и не портит данные.
- Git-команды сводные, по областям, а не по шагам. В `search.py`, `engine.py`, `test_engine.py`, `server.py`, `app.js`, `bots.py`, `eval.py` лежат правки нескольких шагов, их нельзя разделить без интерактивного `git add -p`. Команды из отчётов 0053–0068 **не выполнять**: они дробят файлы ошибочно.
- Порядок коммитов проверен на зависимости:
  - тесты коммита 2 используют только `engine.replay` (есть в HEAD) и собственный парсер фикстур;
  - коммит 3 опирается на `search_root`, `search_top` и `V4_SETTINGS` из коммита 2.
- Co-Authored-By: в команды поставил `Claude Opus 5.5 <noreply@anthropic.com>`, как требует атрибуция среды этого агента. Main просил `Claude Sonnet 5.5`. Если пользователь хочет другую строку, её нужно заменить во всех четырёх коммитах.

## Breaking changes
нет (ревью)

## Проблемы и открытые вопросы
- Критерии на равных оценках (minor 2) — главный риск ложных падений в фазе 9.
- Сила v4 против v3 при 3 с подтверждена только 100 партиями. Этого хватает по п.(5) ROADMAP и по CLAUDE.md, но в фазе 9 новую версию нужно сравнивать с Python-v4 на ≥200 партиях.

## Git (выполняет пользователь)
Сводный набор для **всех** незакоммиченных изменений (git status на момент ревью), **вместо** команд из отчётов 0053–0068. Каждый файл входит ровно в один коммит. Четыре коммита по очереди, PowerShell:
```
git rm .claude/agents/vision-engineer.md tests/fixtures/screens/README.md tests/fixtures/screens/game01_final_a.png tests/fixtures/screens/game01_final_b.png tests/fixtures/screens/game01_start.png
git commit -m "chore(agents): удалены vision-engineer и скриншоты экрана" -m "- распознавание экрана запрещено CLAUDE.md, агент и фикстуры больше не нужны" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"

git add quoridor/search.py quoridor/eval.py quoridor/bitpaths.py quoridor/bots.py quoridor/cli.py quoridor/frozen/search_v4.py tests/search/test_search_root.py tests/search/test_search_delay_loss.py tests/search/test_eval_v4.py tests/search/test_eval_reserve.py tests/bots/test_frozen_v4.py tests/cli/test_cli.py tests/perf/tune.py tests/perf/test_tune.py tests/fixtures/games/game02.txt tests/fixtures/games/game03.txt tests/fixtures/games/game04.txt tests/fixtures/games/game05.txt tests/fixtures/games/game06.txt reports/0053-2026-10-15-ai-engineer-search-root-analyzer.md reports/0059-2026-10-15-ai-engineer-delay-loss-review-mate.md reports/0061-2026-10-16-ai-engineer-v4-bot.md reports/0062-2026-10-16-ai-engineer-v4-game-criteria.md reports/0066-2026-10-16-ai-engineer-v4-reserve-frozen.md
git commit -m "feat(search): бот v4 с запасом стенок, v4-frozen, search_root/search_top" -m "- search_root/search_top: оценки корневых ходов с общим окном и TT; главный поиск на весь бюджет" -m "- проигранная позиция: самое долгое сопротивление, _delay_loss в бюджете хода, delay_slack=2" -m "- v4: гонка стенок, угроза одной стенкой, защитные стенки в корне, PVS+LMR" -m "- reserve_term: стенки соперника в запасе — угроза пути при 0-2 своих (game06 17...-20...)" -m "- quoridor/frozen/search_v4.py: замороженный v4; боты v4-frozen*, v3-3s, v4-3s; v4 по умолчанию в cli" -m "- tune: frozen4:, --max-gap отбрасывает партии со скачком времени (засыпание) и считает их" -m "- 3 с: v4 против v4-frozen 178/116/6 из 300, против v3 64/36/0 из 100; v1-v3 без изменений (A/B)" -m "- партии game02-game06 как фикстуры" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"

git add quoridor/analyzer/engine.py quoridor/analyzer/server.py quoridor/analyzer/static/index.html quoridor/analyzer/static/app.js quoridor/analyzer/static/app.css docs/mockups/analyzer.html tests/analyzer/test_engine.py tests/analyzer/test_server.py tests/analyzer/test_review.py tests/analyzer/test_phase8_e2e.py tests/analyzer/test_client_js.py tests/analyzer/client_harness.js tests/analyzer/test_phase8_v4.py tests/analyzer/test_games_04_06.py reports/0054-2026-10-15-ui-engineer-phase8-nits-evaluate.md reports/0055-2026-10-15-ui-engineer-phase8-review.md reports/0056-2026-10-15-test-engineer-phase8-e2e-game02.md reports/0057-2026-10-15-code-reviewer-phase8.md reports/0060-2026-10-15-ui-engineer-review-0057-nits.md reports/0063-2026-10-16-test-engineer-phase8-v4.md reports/0067-2026-10-16-ui-engineer-default-5s-deep.md reports/0068-2026-10-16-test-engineer-phase8-final.md
git commit -m "feat(analyzer): разбор партии, подсказки v4 5 с, «Глубокий анализ» 20 с" -m "- /api/parse, /api/review: фоновый разбор, метки ?? ? ?! !, точность, график оценки" -m "- листание, ветки и «Вернуть партию», /api/evaluate, /api/cancel" -m "- подсказка: search_top бота v4, 5 с по умолчанию, кнопка «Глубокий анализ» 20 с с отменой" -m "- time проверяется: (0, 30] секунд, иначе 400; разбор до 10 с на позицию" -m "- тесты: разбор, подпроцесс, клиент в Node, критерии фазы 8, game04-06 через API" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"

git add ROADMAP.md CLAUDE.md reports/0058-2026-10-15-main-phase8-close.md reports/0064-2026-10-16-code-reviewer-phase8-v4.md reports/0065-2026-10-16-main-phase8-reorder.md reports/0069-2026-10-16-code-reviewer-phase8-final.md
git commit -m "docs(agents): фаза 8 «Умный бот v4» — критерии, ревью, новая фаза 9" -m "- ROADMAP: v4 — фаза 8 с пересмотренными критериями, фаза 9 «Быстрый движок», разбор партии — 10" -m "- бэклог: оценка эндшпиля со стенками (game02/game03)" -m "- CLAUDE.md: v4 и v4-frozen в реестре, v4 по умолчанию, tune --max-gap" -m "- ревью 0064 и 0069: APPROVE WITH NITS, фазу 8 можно закрывать" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
Получилось четыре коммита: удаление, поиск и бот, анализатор, документы. После коммита 2 тесты `tests/search`, `tests/bots`, `tests/cli`, `tests/perf` проходят на старом `engine.py` из HEAD. Коммит 3 опирается на коммит 2. Если main сразу пишет закрывающий отчёт и меняет «Текущая фаза» в ROADMAP, его отчёт и ROADMAP.md входят в коммит 4 (ROADMAP.md там уже есть).

## Следующий шаг
- **main**: закрыть фазу 8. В ROADMAP поставить «✅ готово (ревью 0069: APPROVE WITH NITS; v4 против v3 при 3 с 64.0%, против v4-frozen 60.3% из 300)», «Текущая фаза: 9. Быстрый движок». Добавить раздел «Первым шагом фазы 9» с minor 1–4 и nit отсюда и открытыми пунктами 0064.
- ui-engineer: minor 1 (гонка cancel/analyze в `app.js`).
- test-engineer: minor 2 и 3, nit про xfail в `test_engine.py`; A/B frozen против HEAD до и после переписывания `bitpaths` (фаза 9).
- ai-engineer: nit 1 (`reserve_term` без вложенной функции), открытые пункты 0064 по `search.py`.
- Пользователь: `v4-frozen-0.2s` в CLAUDE.md:36.

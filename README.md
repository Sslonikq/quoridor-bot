<p align="center"><img src="assets/icon_256.png" alt="Quoridor Analyzer" width="128"></p>

# quoridor-bot

Бот и анализатор для настольной игры **Quoridor** («Коридор»): доска 9×9, по 10 стенок у каждого игрока, цель — дойти до противоположного ряда.
Работает полностью офлайн: ни сетевых запросов, ни телеметрии, анализатор слушает только `127.0.0.1`.

## Как запускать

### Из готового .exe (Windows 10/11, Python не нужен)
1. Скачайте `QuoridorAnalyzer-win64.zip` со страницы [Releases](https://github.com/Sslonikq/quoridor-bot/releases).
2. Распакуйте архив в любую папку (не запускайте из архива).
3. Запустите `QuoridorAnalyzer\QuoridorAnalyzer.exe`. Откроется окно анализатора.
4. Первый анализ после установки занимает около 20–30 секунд (компилируются быстрые ядра, кеш хранится в `%USERPROFILE%\.quoridor\numba-cache`), дальше подсказка приходит за выбранное время (2, 10 или 30 с).

Ярлык на рабочий стол: правая кнопка по `QuoridorAnalyzer.exe` → «Отправить» → «Рабочий стол (создать ярлык)». Из исходников: `powershell -ExecutionPolicy Bypass -File packaging\make_shortcut.ps1`.

Настройки окна (позиция, размер, режим, 📌) лежат в `%USERPROFILE%\.quoridor\analyzer.json`. Удалить программу: удалите папку `QuoridorAnalyzer`, ярлык и `%USERPROFILE%\.quoridor`.

### Из исходников
Нужен Python 3.11+.
```powershell
pip install -e .[fast,app]      # numba (быстрый поиск) и pywebview (окно); оба необязательны
python -m quoridor.analyzer     # анализатор: доска, ходы за обе стороны, подсказки
python -m quoridor.cli          # игра против бота в консоли
```
Без pywebview анализатор откроется в браузере, без numba работает чистый Python (медленнее). Флаги анализатора: `--browser` (обычный браузер), `--no-browser` (только сервер), `--port N`, `--config PATH`.

## Анализатор
- Ходы за обе стороны вводите сами: кликом по доске (ход фишкой, стенка). «＋» или клавиша N — новая партия.
- Подсказки: лучший ход и топ-3 с оценкой; боты «MCTS» (по умолчанию), v5, v4; время «быстро 2 с / нормально 10 с / глубоко 30 с».
- Разбор партии: вставьте ходы с сайта (`d1 e8 c1 …`), листайте клавишами ← → Home End, разбор в фоне размечает ходы `??` `?` `?!` `!`, строит график оценки и точность игроков. Ходом в середине партии создаётся ветка, «Вернуть партию» возвращает основную линию.
- Окно: 📌 поверх всех окон, компактный режим (~380 px) для игры рядом с сайтом.
- Подсказки во время партии — только в дружеских играх, где соперник в курсе. Анализатор ничего не читает с экрана и не кликает за вас.

Нотация: ход фишкой — клетка назначения (`e2`); стенка — клетка-якорь плюс ориентация (`e3h`, `f3v`), якорь — верхняя левая клетка квадрата 2×2. Колонки `a`–`i`, ряды `1`–`9` снизу вверх, белые стартуют с `e1`.

## Архитектура
```
quoridor/
  state.py, rules.py, paths.py, notation.py   # правила, ходы, кратчайшие пути, нотация
  eval.py, search.py, threat.py               # оценка, alpha-beta (v1–v5), угроза комбинацией стенок
  mcts.py, endgame.py                         # PUCT-MCTS (mcts3) и точный решатель концовки
  kernels.py, bitpaths.py, jit.py             # numba-ядра и откат на чистый Python
  cli.py, arena.py, bots.py                   # консоль, матчи ботов, реестр ботов
  analyzer/                                   # сервер (http.server, JSON API), static/, окно pywebview
tests/                                        # правила, пути, нотация, поиск, анализатор, позиции
packaging/                                    # сборка .exe (PyInstaller)
reports/                                      # отчёты по шагам разработки
```
Правила написаны с нуля, без внешних игровых библиотек. Подробности — в `CLAUDE.md`.

## Результаты бота
Матчи через `python -m quoridor.arena` и `python -m tests.perf.tune` (при равном времени на ход):
- v3 против жадного бота: 50/0/0; против v1: 152/39/9.
- v4 против v3 при 3 с: 64/36 из 100; против замороженного v4: 178/116/6 из 300 (60,3%).
- mcts3 против v4 при 10 с: 111–89 из 200 (55,5%, доверительный интервал Уилсона ≈ 48,6–62,3%): впереди, но статистически не доказано.
- Набор реальных позиций из проигранных партий: mcts3 решает 95% при 3 с и 100% при 10 с (медиана из трёх прогонов).
- По словам автора, 10 партий подряд против живого соперника по подсказкам сыграны без проигрышей.

## Разработка
```powershell
pip install -e .[dev,fast,app]
pytest -q                      # все тесты (QUORIDOR_NO_NUMBA=1 — на чистом Python)
pytest -q -m "not slow"        # без медленных тестов
ruff check . ; ruff format .
python -m quoridor.arena --games 100 --a v4 --b v3
```

## Сборка .exe
```powershell
pip install pyinstaller
python -m PyInstaller packaging/quoridor_analyzer.spec --noconfirm --distpath dist --workpath build
Compress-Archive -Path dist\QuoridorAnalyzer -DestinationPath dist\QuoridorAnalyzer-win64.zip -Force
```
Результат — `dist\QuoridorAnalyzer\QuoridorAnalyzer.exe` (папка около 190 МБ, архив около 74 МБ). Папки `dist/` и `build/` в `.gitignore`: .exe публикуется в GitHub Releases, а не в репозиторий (команды — `packaging/release.md`).

## Лицензия
[MIT](LICENSE).

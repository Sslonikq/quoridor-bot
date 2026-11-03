# 0114 · README, LICENSE и сборка .exe (фаза 13)

- **Дата:** 2026-11-03
- **Агент:** main
- **Статус:** done
- **Связанные отчёты:** 0107, 0109, 0110, 0111, 0112, 0113

## Задача
Фаза 13: README (установка, запуск, архитектура, результаты), LICENSE (MIT), сборка .exe через PyInstaller, ярлык на рабочий стол, подготовка публикации в GitHub Releases (публикует пользователь).

## Что сделано
- Первым шагом закрыты minor/nits ревью 0110: ui-engineer — 0112 (`try` вокруг `form.Invoke`, `sys.modules.get` вместо импорта WinForms, докстринг про 📌); test-engineer — 0113 (тесты фильтра `-32000`, сегментов в компакте, событий окна, позднего `pywebviewready`).
- Пользователь подтвердил вручную: настройки окна сохраняются после перезапуска, процесс python завершается при закрытии окна (закрывает п. 3 «Первым шагом фазы 13»).
- `pip install pyinstaller` (с согласия пользователя).
- `packaging/launcher.py` — точка входа .exe: до импорта numba переносит кеш ядер в `~/.quoridor/numba-cache` (распакованный бандл временный), дальше обычный `quoridor.analyzer.main()`.
- `packaging/quoridor_analyzer.spec` — onedir-сборка, `console=False`; numba, llvmlite, pywebview, pythonnet собираются целиком (`collect_all`), `quoridor` кладётся как `pyz+py`, потому что `cache=True` в numba требует исходники рядом с байткодом; `static/` как данные; без tkinter, pytest, hypothesis, ruff.
- `packaging/make_shortcut.ps1` — ярлык «Quoridor Analyzer» на рабочем столе (создан).
- `packaging/release.md` и `packaging/release-notes.md` — готовая команда `gh release create` и описание релиза: что скачать и как запустить.
- `README.md` переписан: как запускать (.exe и исходники), анализатор, архитектура, результаты, разработка, сборка. `LICENSE` (MIT) — пользователь поправил правообладателя на «Sslonik».

## Изменённые файлы
| Файл | Что изменилось |
|---|---|
| `README.md` | полностью переписан |
| `LICENSE` | MIT |
| `packaging/launcher.py` | точка входа .exe, кеш numba в `~/.quoridor` |
| `packaging/quoridor_analyzer.spec` | сборка PyInstaller |
| `packaging/make_shortcut.ps1` | ярлык на рабочий стол |
| `packaging/release.md`, `packaging/release-notes.md` | команды `gh` и описание релиза |
| `ROADMAP.md` | фаза 13 закрыта, текущая — следующая (после ревью) |

`dist/` и `build/` уже в `.gitignore`; архив в репозиторий не идёт.

## Проверка
- Тесты: `pytest -q -m "not slow"` → 1051 passed, 2 xfailed, 193 deselected.
- Линт: `ruff check .` → ok; `ruff format --check .` → 217 files already formatted.
- .exe (собран локально, `dist/QuoridorAnalyzer`, 191 МБ, zip 73,7 МБ):
  - `--no-browser --port 8765`: `/` и `/app.js` → 200, слушает только `127.0.0.1`; `/api/analyze` (mcts, 2 с) → ok, 89 676 симуляций, то есть numba в бандле работает;
  - холодный первый анализ 23,8 с (компиляция ядер в пустой кеш), повторный 2,07 с;
  - режим окна: заголовок «Анализатор Коридора», после `CloseMainWindow` процесс завершился. Остановка тестовых процессов — по PID.
- Перфоманс кода не менялся. Матч в arena не нужен (сила бота не менялась).
- Не проверено: сборка и запуск на машине без Python и без установленного .NET/WebView2 (проверка на другой машине — за пользователем или другом).

## Решения и почему
- onedir, не onefile: onefile распаковывается в temp при каждом запуске (+секунды, ложные срабатывания антивирусов), а numba и так тяжёлая.
- numba остаётся в сборке: без неё MCTS заметно слабее на том же времени.
- Кеш ядер вне бандла: иначе компиляция при каждом запуске.
- Прокси: в Git Bash `curl` без `--noproxy "*"` уходит в системный прокси и получает 503. Сам анализатор ни при чём.

## Breaking changes
нет.

## Проблемы и открытые вопросы
- Файл не подписан: Windows SmartScreen покажет предупреждение (в `release-notes.md` описано, как пройти).
- Первый запуск после установки: 20–30 с на компиляцию ядер, об этом сказано в README и описании релиза. Возможное улучшение — прогрев кеша при старте в фоне.
- В `release-notes.md` версия `v1.0.0` и репозиторий `Sslonikq/quoridor-bot` взяты из `git remote`; поправить, если нужны другие.
- Установлено: pyinstaller 6.22.3, altgraph 0.17.5, pefile 2024.8.26, pywin32-ctypes 0.2.3, pyinstaller-hooks-contrib 2026.9. Удаление: `pip uninstall pyinstaller pyinstaller-hooks-contrib altgraph pefile pywin32-ctypes`. Артефакты сборки: `Remove-Item -Recurse dist, build`.

## Git (выполняет пользователь)
```
git add README.md LICENSE packaging/launcher.py packaging/quoridor_analyzer.spec packaging/make_shortcut.ps1 packaging/release.md packaging/release-notes.md ROADMAP.md reports/0114-2026-11-03-main-readme-exe.md
git commit -m "feat(cli): README, LICENSE и сборка .exe через PyInstaller" -m "- README: запуск из .exe и из исходников, архитектура, результаты" -m "- packaging: spec, launcher с кешем numba в ~/.quoridor, ярлык" -m "- release.md: команда gh release create и описание релиза"
```

## Следующий шаг
code-reviewer: ревью фазы 13 (0112–0114). Затем пользователь публикует релиз командой из `packaging/release.md`.

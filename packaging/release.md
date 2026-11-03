# Публикация релиза (выполняет владелец репозитория)

Перед релизом код должен быть запушен (`git push`), иначе тег встанет на старый коммит. Нужен [GitHub CLI](https://cli.github.com/) (`gh`) и вход: `gh auth login`. Сборка и архив: см. раздел «Сборка .exe» в README.

```powershell
git push
gh release create v1.0.0 dist\QuoridorAnalyzer-win64.zip --repo Sslonikq/quoridor-bot --title "Quoridor Analyzer 1.0.0" --notes-file packaging\release-notes.md
```

Обновить архив у существующего релиза: `gh release upload v1.0.0 dist\QuoridorAnalyzer-win64.zip --clobber`.
Удалить релиз: `gh release delete v1.0.0 --yes --cleanup-tag`.

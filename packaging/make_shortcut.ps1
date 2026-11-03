# Creates a desktop shortcut "Quoridor Analyzer".
# powershell -ExecutionPolicy Bypass -File packaging\make_shortcut.ps1 [-ExeDir "path\to\QuoridorAnalyzer"]
param(
    [string]$ExeDir = (Join-Path (Split-Path $PSScriptRoot -Parent) "dist\QuoridorAnalyzer")
)

$exe = Join-Path $ExeDir "QuoridorAnalyzer.exe"
if (-not (Test-Path $exe)) {
    Write-Error "Not found: $exe (build the app first or pass -ExeDir)"
    exit 1
}
$link = Join-Path ([Environment]::GetFolderPath("Desktop")) "Quoridor Analyzer.lnk"
$sh = New-Object -ComObject WScript.Shell
$s = $sh.CreateShortcut($link)
$s.TargetPath = $exe
$s.WorkingDirectory = $ExeDir
$s.IconLocation = "$exe,0"
$s.Description = "Quoridor analyzer (offline)"
$s.Save()
Write-Host "Shortcut created: $link"

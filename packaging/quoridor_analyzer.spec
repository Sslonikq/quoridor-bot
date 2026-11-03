# PyInstaller spec: pyinstaller packaging/quoridor_analyzer.spec --noconfirm
# Run from the repository root. Output: dist/QuoridorAnalyzer/ (onedir) and a zip of it.
import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
datas = [(os.path.join(ROOT, "quoridor", "analyzer", "static"), "quoridor/analyzer/static"),
         (os.path.join(ROOT, "assets", "icon.ico"), "assets")]
binaries = []
hiddenimports = collect_submodules("quoridor")
for pkg in ("numba", "llvmlite", "webview", "clr_loader", "pythonnet"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [os.path.join(SPECPATH, "launcher.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    # numba cache=True needs the .py sources of the kernels next to the bytecode
    module_collection_mode={"quoridor": "pyz+py"},
    excludes=["tkinter", "pytest", "hypothesis", "ruff"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="QuoridorAnalyzer",
    console=False,
    icon=os.path.join(ROOT, "assets", "icon.ico"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="QuoridorAnalyzer")

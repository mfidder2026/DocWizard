# PyInstaller spec — builds DocWizard.app (macOS) or DocWizard.exe (Windows) from the same source.
# Usage:  uv run pyinstaller packaging/docwizard.spec --noconfirm
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
datas = [(str(ROOT / "src/docwizard/web"), "docwizard/web")]
datas += collect_data_files("trafilatura") + collect_data_files("justext") if True else []
hidden = collect_submodules("docwizard") + collect_submodules("uvicorn") + ["webview"]

a = Analysis([str(ROOT / "packaging/launch.py")], pathex=[str(ROOT / "src")], datas=datas,
             hiddenimports=hidden, excludes=["tkinter", "pytest"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="DocWizard", console=False,
          icon=str(ROOT / ("packaging/icon.icns" if sys.platform == "darwin" else "packaging/icon.ico"))
          if (ROOT / "packaging/icon.icns").exists() else None)
coll = COLLECT(exe, a.binaries, a.datas, name="DocWizard")
if sys.platform == "darwin":
    app = BUNDLE(coll, name="DocWizard.app", bundle_identifier="app.fremen.docwizard",
                 info_plist={"CFBundleShortVersionString": "0.2.1", "NSHighResolutionCapable": True,
                             "LSMinimumSystemVersion": "12.0"})

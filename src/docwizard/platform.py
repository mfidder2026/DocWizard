"""OS-specific helpers: data directory and opening files. All OS calls live here."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

APP_NAME = "DocWizard"


def default_data_dir() -> Path:
    """Per-OS data directory; `DOCWIZARD_HOME` overrides (tests, portable installs)."""
    env = os.environ.get("DOCWIZARD_HOME")
    if env:
        return Path(env).expanduser()
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / APP_NAME
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        return (Path(appdata) if appdata else home / "AppData" / "Roaming") / APP_NAME
    xdg = os.environ.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else home / ".local" / "share") / APP_NAME


def open_with_default_app(path: str | Path) -> None:
    """Open a file with the OS default application (e.g. a .docx in Word)."""
    p = str(Path(path).resolve())
    if sys.platform == "darwin":
        subprocess.Popen(["open", p])
    elif sys.platform == "win32":
        os.startfile(p)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", p])


def reveal_in_finder(path: str | Path) -> None:
    """Show the file in Finder / Explorer (Linux: open the containing folder)."""
    p = Path(path).resolve()
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(p)])
    elif sys.platform == "win32":
        subprocess.Popen(["explorer", f"/select,{p}"])
    else:
        subprocess.Popen(["xdg-open", str(p if p.is_dir() else p.parent)])

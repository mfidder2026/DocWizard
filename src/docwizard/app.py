"""Desktop launcher: engine on 127.0.0.1 + native window (pywebview), browser as fallback.

macOS uses WKWebView, Windows uses Edge WebView2 — same code on both.
"""
from __future__ import annotations

import argparse
import logging
import socket
import sys
import threading
import time
import webbrowser

import httpx
import uvicorn


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def ensure_streams() -> None:
    """Windowed builds (PyInstaller console=False on Windows) have no stdout/stderr: sys.stdout is None,
    which crashes anything that writes to or inspects it (uvicorn's log formatter calls isatty()).
    Send output to a log file in the data directory instead."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        from .config import app_paths
        log = open(app_paths().logs / "docwizard.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    except OSError:
        import os
        log = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stdout is None:
        sys.stdout = log
    if sys.stderr is None:
        sys.stderr = log


def main() -> None:
    ap = argparse.ArgumentParser(prog="docwizard", description="DocWizard — lokale documentenwizard")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--browser", action="store_true", help="open in de standaardbrowser i.p.v. een eigen venster")
    ap.add_argument("--server-only", action="store_true", help="alleen de engine starten")
    args = ap.parse_args()

    ensure_streams()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    from .server.app import TOKEN, app

    port = args.port or _free_port()
    # log_config=None: use our logging setup, not uvicorn's console formatter.
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", loop="asyncio",
                            log_config=None)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}/"
    for _ in range(100):
        try:
            if httpx.get(url + "api/health", timeout=0.5).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    print(f"DocWizard draait op {url}", flush=True)

    if args.server_only:
        thread.join()
        return
    if not args.browser:
        try:
            _open_window(url)
        except Exception:  # noqa: BLE001 — never leave the user without a UI
            logging.getLogger("docwizard").exception("Eigen venster kon niet starten; standaardbrowser gebruikt")
            args.browser = True
    if args.browser:
        webbrowser.open(url)
        try:
            thread.join()
        except KeyboardInterrupt:
            pass
    server.should_exit = True
    thread.join(timeout=5)
    _ = TOKEN


def unblock_bundled_files() -> int:
    """Windows marks files extracted from a downloaded zip as 'from the internet' (Mark of the Web).
    .NET Framework then refuses to load Python.Runtime.dll, which pywebview needs. Remove the mark from
    our own bundled files when we are allowed to (per-user installs; not under Program Files)."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return 0
    import os
    from pathlib import Path
    base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    removed = 0
    for f in base.rglob("*"):
        if f.suffix.lower() in (".dll", ".exe", ".pyd") and f.is_file():
            try:
                os.remove(f"{f}:Zone.Identifier")
                removed += 1
            except OSError:
                pass
    return removed


def _open_window(url: str) -> None:
    n = unblock_bundled_files()
    if n:
        logging.getLogger("docwizard").info("Downloadmarkering verwijderd van %d bestanden", n)
    import webview  # pywebview: WKWebView on macOS, Edge WebView2 on Windows
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.create_window("DocWizard", url, width=1440, height=920, min_size=(1000, 640), text_select=True)
    webview.start(private_mode=False)


if __name__ == "__main__":
    main()

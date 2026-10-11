"""Desktop launcher: engine on 127.0.0.1 + native window (pywebview), browser as fallback.

macOS uses WKWebView, Windows uses Edge WebView2 — same code on both.
"""
from __future__ import annotations

import argparse
import logging
import socket
import threading
import time
import webbrowser

import httpx
import uvicorn


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    ap = argparse.ArgumentParser(prog="docwizard", description="DocWizard — lokale documentenwizard")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--browser", action="store_true", help="open in de standaardbrowser i.p.v. een eigen venster")
    ap.add_argument("--server-only", action="store_true", help="alleen de engine starten")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    from .server.app import TOKEN, app

    port = args.port or _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", loop="asyncio")
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
            import webview  # pywebview
        except ImportError:
            args.browser = True
    if args.browser:
        webbrowser.open(url)
        try:
            thread.join()
        except KeyboardInterrupt:
            pass
        server.should_exit = True
        return
    webview.settings["ALLOW_DOWNLOADS"] = True
    window = webview.create_window("DocWizard", url, width=1440, height=920, min_size=(1000, 640),
                                   text_select=True)
    webview.start(private_mode=False)
    server.should_exit = True
    thread.join(timeout=5)
    _ = (window, TOKEN)


if __name__ == "__main__":
    main()

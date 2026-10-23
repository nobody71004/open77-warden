"""The Warden app's local web server: the home page and one address per server panel (proxy.py),
all on this machine's loopback, all on threads of this one process.

The window (the Windows host) starts it with --ready-file and opens the URL written there; it can
also run on its own (python -m fivem2open77.warden serve --open) and be used from a browser.
"""
from __future__ import annotations

import json
import os
import secrets as pysecrets
import threading
import time

from . import VERSION
from . import config as C
from .app import WardenApp
from .logbook import log, warn
from .proxy import LOCAL, bind_panel


def serve(port=0, workspace=None, ready_file=None, token=None, open_browser=False, on_ready=None, stop=None):
    """Runs until interrupted (or ``stop``, a threading.Event, is set: the tests)."""
    ws = os.path.abspath(workspace or os.environ.get("O77W_WORKSPACE") or C.default_workspace())
    token = token or os.environ.get("O77W_TOKEN") or pysecrets.token_urlsafe(24)
    app = WardenApp(ws, token)
    s = app.settings.get()
    want = port or s["home_port"] or C.HOME_PORT
    home = bind_panel(app, None, token, "warden", want)
    app.home = home
    if home.port != s["home_port"]:
        app.settings.update({"home_port": home.port})
    threading.Thread(target=home.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True, name="home").start()
    try:
        app.refresh_panels()
    except Exception as e:  # noqa: BLE001 -- the home page says what is wrong
        warn(f"the panels could not all start: {e}", src="app")
    to = LOCAL + "/"
    url = f"{home.origin}{LOCAL}/auth?token={token}&to={to}"
    info = {"port": home.port, "token": token, "url": url, "origin": home.origin, "pid": os.getpid(),
            "workspace": ws, "version": VERSION}
    if ready_file:
        tmp = ready_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(info, f)
        os.replace(tmp, ready_file)
    log(f"home page on {home.origin}{LOCAL}/ ({len(app.panels)} server panel(s))", src="app")
    if on_ready is not None:
        on_ready(app, home)
    if open_browser:
        import webbrowser
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        while not (stop.wait(1.0) if stop is not None else time.sleep(3600)):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        app.close()
        home.shutdown()
        home.server_close()
    return 0

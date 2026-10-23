"""python -m fivem2open77.warden serve [--port N] [--workspace DIR] [--ready-file F] [--open]"""
from __future__ import annotations

import argparse
import sys


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m fivem2open77.warden", description="Open77 Warden: the server panel with a live map")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("serve", help="run the Warden app's local server (what the window shows)")
    s.add_argument("--port", type=int, default=0, help="the home page's port (0: a free one)")
    s.add_argument("--workspace", default=None, help="settings, caches and logs (default: %%LOCALAPPDATA%%\\open77-warden)")
    s.add_argument("--ready-file", default=None, help="written with the URL and port once listening")
    s.add_argument("--token", default=None, help="the session token (default: random)")
    s.add_argument("--open", action="store_true", help="open the default browser")
    a = ap.parse_args(argv)
    if a.cmd != "serve":
        ap.print_help()
        return 2
    from .server import serve
    return serve(port=a.port, workspace=a.workspace, ready_file=a.ready_file, token=a.token, open_browser=a.open)


if __name__ == "__main__":
    sys.exit(main())

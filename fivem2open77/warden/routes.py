"""The app's own pages and API, under /__o77w/ on every panel address (and the home one).

    /__o77w/auth?token=...&to=...    the session cookie for this address, then the page asked for
    /__o77w/...                      the app's pages (web/), /__o77w/studio/... the Studio's (the 3D city)
    /__o77w/capture.js               the script the panel pages get (no sign-in: Warden's pages load it)
    /__o77w/api/...                  the API (the session cookie; a cross-site request is refused)
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import secrets as pysecrets
import time
import urllib.parse

from ..studio import jsonutil
from ..studio.paths import web_dir as studio_web_dir
from ..studio.projects import ProjectError
from ..studio.settings import SettingsError, validate_server
from . import logbook
from .logbook import log
from .proxy import COOKIE, LOCAL
from .tunnel import TunnelError

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
         ".json": "application/json", ".woff2": "font/woff2", ".txt": "text/plain; charset=utf-8", ".wasm": "application/wasm"}
CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
       "connect-src 'self' blob: data:; worker-src 'self' blob:; frame-src 'self'; frame-ancestors 'self'")
ROUTES = []
_DIAG = {"t": 0.0, "n": 0}


class HttpError(Exception):
    def __init__(self, status, message, extra=None):
        super().__init__(message)
        self.status = status
        self.extra = extra or {}


def route(method, pattern):
    rx = re.compile("^" + LOCAL + "/api" + pattern + "$")

    def deco(fn):
        ROUTES.append((method, rx, fn))
        return fn
    return deco


def _json(h, obj, status=200, headers=None):
    h.send_plain(status, jsonutil.dumps_bytes(obj), "application/json", headers)


def _body(h) -> dict:
    raw = h.body()
    if not raw:
        return {}
    try:
        v = json.loads(raw)
    except ValueError as e:
        raise HttpError(400, f"invalid JSON: {e}") from e
    if not isinstance(v, dict):
        raise HttpError(400, "a JSON object is expected")
    return v


def _file(h, path, ctype, immutable=False):
    if not os.path.isfile(path):
        raise HttpError(404, "not found")
    size = os.path.getsize(path)
    h.send_response(200)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(size))
    h.send_header("Cache-Control", "private, max-age=31536000, immutable" if immutable else "no-store")
    h.end_headers()
    if h.command == "HEAD":
        return
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            h.wfile.write(chunk)


def _static(h, root, rel):
    rel = urllib.parse.unquote(rel).lstrip("/") or "index.html"
    full = os.path.realpath(os.path.join(root, rel))
    if not full.startswith(os.path.realpath(root) + os.sep) or not os.path.isfile(full):
        return h.send_plain(404, b"not found", "text/plain")
    ctype = TYPES.get(os.path.splitext(full)[1].lower()) or mimetypes.guess_type(full)[0] or "application/octet-stream"
    with open(full, "rb") as f:
        data = f.read()
    headers = {"Content-Security-Policy": CSP} if full.endswith(".html") else None
    h.send_plain(200, data, ctype, headers)


def dispatch(h):
    url = urllib.parse.urlsplit(h.path)
    path = url.path
    q = {k: v[-1] for k, v in urllib.parse.parse_qs(url.query).items()}
    try:
        if path == LOCAL:
            return h.send_plain(302, b"", "text/plain", {"Location": LOCAL + "/"})
        if path == LOCAL + "/auth":
            tok = q.get("token", "")
            if not pysecrets.compare_digest(tok, h.server.token):
                return h.send_plain(403, b"bad token", "text/plain")
            to = q.get("to") or LOCAL + "/"
            if not to.startswith(LOCAL + "/") or "//" in to[1:] or "\\" in to:
                to = LOCAL + "/"
            return h.send_plain(302, b"", "text/plain", {
                "Location": to, "Set-Cookie": f"{COOKIE}={h.server.token}; Path={LOCAL}; HttpOnly; SameSite=Strict"})
        if path == LOCAL + "/capture.js":
            return _static(h, WEB, "capture.js")
        if path.startswith(LOCAL + "/api/"):
            if path != LOCAL + "/api/health":
                if not h.token_ok():
                    return _json(h, {"error": "not signed in to the app: open it from its window"}, 401)
                if h.command not in ("GET", "HEAD") and not h.origin_ok():
                    return _json(h, {"error": "cross-site request refused"}, 403)
            for method, rx, fn in ROUTES:
                if method != h.command and not (method == "GET" and h.command == "HEAD"):
                    continue
                m = rx.match(path)
                if m:
                    return fn(h, q, *m.groups())
            raise HttpError(404, "no such endpoint")
        if path.startswith(LOCAL + "/studio/"):
            return _static(h, studio_web_dir(), path[len(LOCAL + "/studio/"):])
        return _static(h, WEB, path[len(LOCAL) + 1:])
    except HttpError as e:
        return _json(h, {"error": str(e), **e.extra}, e.status)
    except SettingsError as e:
        return _json(h, {"error": "invalid settings", "errors": e.errors}, 400)
    except TunnelError as e:
        return _json(h, {"error": str(e), "reachable": False}, 502)
    except (ProjectError, ValueError, KeyError) as e:
        return _json(h, {"error": str(e) or e.__class__.__name__}, 400)


def _num(q, k, default=None):
    v = q.get(k)
    if v in (None, ""):
        return default
    try:
        return float(v)
    except ValueError:
        raise HttpError(400, f"{k}: a number") from None


def _here(h):
    t = h.server.target
    return t["key"] if t else None


# ---------------------------------------------------------------------- the app
@route("GET", r"/health")
def api_health(h, q):
    _json(h, {"ok": True, "app": "open77-warden"})


@route("GET", r"/state")
def api_state(h, q):
    _json(h, h.app.state(here=_here(h)))


@route("POST", r"/targets/(.+)/check")
def api_check(h, q, key):
    _json(h, h.app.check(urllib.parse.unquote(key)))


@route("POST", r"/last")
def api_last(h, q):
    key = str(_body(h).get("key") or "")
    h.app.settings.update({"last": key})
    _json(h, {"ok": True})


@route("GET", r"/settings")
def api_settings(h, q):
    s = h.app.settings.get()
    for srv in s["servers"]:
        srv["password_set"] = h.app.own_secrets.has(f"server:{srv['id']}:password")
        srv["passphrase_set"] = h.app.own_secrets.has(f"server:{srv['id']}:passphrase")
    _json(h, s)


@route("PATCH", r"/settings")
def api_settings_patch(h, q):
    b = _body(h)
    patch = {k: b[k] for k in ("paths", "studio", "poll_seconds", "trail_seconds") if k in b}
    h.app.settings.update(patch)
    h.app.toolbox(refresh=True)
    log(f"settings changed: {', '.join(sorted(patch)) or 'nothing'}", src="app")
    _json(h, {"ok": True})


@route("POST", r"/servers")
def api_server_put(h, q):
    """A server of this app's own (the Studio's form); its password apart (never sent back)."""
    b = _body(h)
    pw, pp = b.pop("password", None), b.pop("passphrase", None)
    srv = validate_server(b)
    s = h.app.settings.get()
    servers = [x for x in s["servers"] if x["id"] != srv["id"]] + [srv]
    h.app.settings.update({"servers": servers})
    if pw is not None:
        h.app.own_secrets.set(f"server:{srv['id']}:password", pw or None)
    if pp is not None:
        h.app.own_secrets.set(f"server:{srv['id']}:passphrase", pp or None)
    log(f"server {srv['id']} saved ({srv['user']}@{srv['host']}:{srv['port']}, {', '.join(srv['names'])})", src="app")
    h.app.refresh_panels()
    _json(h, {"ok": True, "id": srv["id"]})


@route("DELETE", r"/servers/([a-z0-9_-]{1,32})")
def api_server_delete(h, q, sid):
    s = h.app.settings.get()
    h.app.settings.update({"servers": [x for x in s["servers"] if x["id"] != sid]})
    for k in ("password", "passphrase"):
        h.app.own_secrets.set(f"server:{sid}:{k}", None)
    h.app.refresh_panels()
    log(f"server {sid} removed", src="app")
    _json(h, {"ok": True})


@route("GET", r"/log")
def api_log(h, q):
    _json(h, logbook.since(int(_num(q, "since", 0) or 0)))


@route("POST", r"/log")
def api_log_post(h, q):
    """The page's own events (the roster's shape, a refused action...): one line each, 120 a minute."""
    b = _body(h)
    now = time.time()
    if now - _DIAG["t"] > 60:
        _DIAG.update(t=now, n=0)
    _DIAG["n"] += 1
    if _DIAG["n"] <= 120:
        lvl = b.get("level") if b.get("level") in ("info", "warn", "error") else "info"
        text = re.sub(r"[\x00-\x1f]+", " ", str(b.get("text") or ""))[:1500]
        logbook.log(text, level=lvl, src=f"page:{(_here(h) or 'home')}")
    _json(h, {"ok": True})


@route("POST", r"/diag")
def api_diag(h, q):
    b = _body(h)
    text = re.sub(r"[\x00-\x1f]+", " ", f"{b.get('kind')}: {b.get('message')} [{b.get('page')}]")[:800]
    logbook.log(text, level="warn", src="page")
    _json(h, {"ok": True})


@route("GET", r"/jobs/([a-f0-9]{12})")
def api_job(h, q, jid):
    j = h.app.jobs.get(jid)
    if not j:
        raise HttpError(404, "no such job")
    _json(h, j.detail(int(q.get("since", 0))))


@route("POST", r"/jobs/([a-f0-9]{12})/cancel")
def api_job_cancel(h, q, jid):
    j = h.app.jobs.get(jid)
    if not j:
        raise HttpError(404, "no such job")
    _json(h, {"cancelled": j.cancel()})


# ---------------------------------------------------------------------- Night City
@route("GET", r"/citymap")
def api_citymap_status(h, q):
    _json(h, h.app.citymap_status())


@route("POST", r"/citymap/build")
def api_citymap_build(h, q):
    _json(h, h.app.citymap_build(force=bool(_body(h).get("force"))).summary())


@route("GET", r"/citymap/(citymap\.json|citymap\.bin)")
def api_citymap_file(h, q, name):
    ctype = "application/json" if name.endswith(".json") else "application/octet-stream"
    _file(h, h.app.citymap_file(name), ctype, immutable=bool(q.get("k")))


@route("GET", r"/city/detail")
def api_city_detail(h, q):
    cam = None
    if all(k in q for k in ("cx", "cy", "cz")):
        cam = (_num(q, "cx", 0.0), _num(q, "cy", 0.0), _num(q, "cz", 0.0))
    _json(h, h.app.city_detail(_num(q, "x", 0.0), _num(q, "y", 0.0), near=_num(q, "near"), far=_num(q, "far"),
                               cam=cam, k=_num(q, "k"), reach=_num(q, "reach"), detail=_num(q, "d"),
                               max_meshes=_num(q, "m")))


@route("GET", r"/city/geo/([0-9a-f]{16})")
def api_city_geo(h, q, gid):
    _file(h, h.app.geo_file(gid), "application/octet-stream", immutable=True)


@route("GET", r"/go/(.+)")
def api_go(h, q, key):
    """Where to open a panel (or the home page, key "home"), signed in: the address and its session."""
    key = urllib.parse.unquote(key)
    app = h.app
    if key == "home":
        origin = app.home.origin
    else:
        p = app.panel(key)
        if p is None:
            raise HttpError(404, "no such server")
        origin = p["server"].origin
        app.settings.update({"last": key})
    page = q.get("page") or ""
    to = LOCAL + "/" + (("#" + re.sub(r"[^a-z0-9/_-]", "", page.lower())) if page else "")
    _json(h, {"url": f"{origin}{LOCAL}/auth?token={h.server.token}&to={urllib.parse.quote(to, safe='/#')}", "origin": origin})

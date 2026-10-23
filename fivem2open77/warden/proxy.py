"""Each server's Warden panel as a site of its own on this machine, and the app's pages beside it.

A panel listens on http://<slug>.localhost:<port>/ (Chromium sends every *.localhost name to this
machine).  Everything there is the server's own Warden, reached through the server's SSH
connection (tunnel.py) and seen by Warden as a browser on the server host itself: Host and Origin
say localhost:<Warden's port>, exactly what an ``ssh -L`` tunnel gives, so Warden's own sign-in,
anti-forgery and same-origin checks hold.  Under /__o77w/ the same address serves the app's own
pages and API: they share the panel's origin, so the live map reads Warden's roster with the
operator's own sign-in and shows the panel inside the app (in a frame of its own origin).

What changes on the way back: redirects to Warden's own address come back to this one; a cookie's
Domain is dropped (it stays on this name); a page may be framed by its own origin only (Warden's
"never frame me" becomes "only my origin may"); Warden's HTML gets one script of the app's
(/__o77w/capture.js, same origin: what the panel sends to its own API, so the app sends the same).
Nothing secret is logged: requests are logged as method, path and status.

The home listener (http://warden.localhost:<port>/) has no Warden behind it: the app's pages only.
"""
from __future__ import annotations

import http.client
import io
import re
import secrets as pysecrets
import socket
import threading
import time
import urllib.parse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .logbook import log, warn
from .tunnel import TunnelError

HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
       "transfer-encoding", "upgrade", "proxy-connection"}
LOCAL = "/__o77w"
COOKIE = "o77w"
INJECT_MAX = 8 << 20                    # HTML bigger than this passes untouched
UPSTREAM_TIMEOUT = 90.0                 # seconds Warden may take to answer (a long stream keeps going)


class _ChanRaw(io.RawIOBase):
    """A paramiko channel as a raw stream (so http.client gets a real BufferedReader: read1, peek)."""

    def __init__(self, chan):
        self.chan = chan

    def readable(self):
        return True

    def readinto(self, b):
        data = self.chan.recv(len(b))
        n = len(data)
        b[:n] = data
        return n


class ChannelSocket:
    """Enough of a socket for http.client, over a paramiko channel."""

    def __init__(self, chan):
        self.chan = chan

    def sendall(self, data):
        self.chan.sendall(data)

    def makefile(self, mode="rb", buffering=None, **kw):
        return io.BufferedReader(_ChanRaw(self.chan), 65536)

    def settimeout(self, t):
        self.chan.settimeout(t)

    def close(self):
        try:
            self.chan.close()
        except Exception:   # noqa: BLE001 -- already closed
            pass


def _split_cookie_header(v: str) -> list:
    return [p.strip() for p in (v or "").split(";") if p.strip()]


def rewrite_csp(v: str) -> str:
    """frame-ancestors 'none' (or a list without this origin) -> 'self': the app frames the panel
    from the panel's own origin; no other page may."""
    parts = [p.strip() for p in v.split(";") if p.strip()]
    out, had = [], False
    for p in parts:
        if p.lower().startswith("frame-ancestors"):
            out.append("frame-ancestors 'self'")
            had = True
        else:
            out.append(p)
    return "; ".join(out)


def script_nonce(csp: str | None) -> str | None:
    """A CSP that allows scripts by nonce: the nonce, for the app's one script in the page."""
    if not csp:
        return None
    for p in csp.split(";"):
        p = p.strip()
        if p.lower().startswith("script-src") or p.lower().startswith("default-src"):
            m = re.search(r"'nonce-([^']+)'", p)
            if m:
                return m.group(1)
    return None


def inject_capture(html: bytes, nonce: str | None) -> bytes:
    tag = (f'<script src="{LOCAL}/capture.js"' + (f' nonce="{nonce}"' if nonce else "") + "></script>").encode()
    m = re.search(rb"<head[^>]*>", html[:65536], re.I)
    if m:
        return html[:m.end()] + tag + html[m.end():]
    m = re.search(rb"<html[^>]*>", html[:65536], re.I)
    if m:
        return html[:m.end()] + tag + html[m.end():]
    return tag + html


class PanelServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False         # (two apps must not share a port: the second one moves)
    request_queue_size = 64

    def __init__(self, addr, app, target, token, slug):
        self.app = app
        self.target = target            # None: the home listener
        self.token = token
        self.slug = slug
        super().__init__(addr, PanelHandler)
        self.port = self.server_address[1]
        self.origin = f"http://{slug}.localhost:{self.port}"
        self.hosts = {f"{slug}.localhost:{self.port}"}
        if target is None:
            self.hosts |= {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        self.requests = 0
        self.errors = 0
        self.statuses = {}              # (method, path) -> the last status (the log says changes)
        self.last_status = None
        self.last_at = 0.0


class PanelHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "open77-warden"
    timeout = 300                        # an idle keep-alive connection closes after this long

    def log_message(self, fmt, *args):  # (requests are logged by _forward, without query strings)
        pass

    def setup(self):
        super().setup()
        self.upstream = None             # http.client connection to Warden, kept with this connection

    def finish(self):
        try:
            super().finish()
        finally:
            self._drop_upstream()

    # ------------------------------------------------------------------ plumbing
    @property
    def app(self):
        return self.server.app

    def send_plain(self, status, body: bytes, ctype="application/json", headers=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def host_ok(self) -> bool:
        return (self.headers.get("Host") or "").lower() in self.server.hosts

    def origin_ok(self) -> bool:
        o = self.headers.get("Origin")
        return o is None or o.lower() == self.server.origin

    def token_ok(self) -> bool:
        tok = self.server.token
        h = self.headers.get("X-O77W-Token")
        if h and pysecrets.compare_digest(h, tok):
            return True
        c = SimpleCookie()
        try:
            c.load(self.headers.get("Cookie") or "")
        except Exception:   # noqa: BLE001 -- a cookie this parser does not like: not ours then
            return False
        return COOKIE in c and pysecrets.compare_digest(c[COOKIE].value, tok)

    def body(self, limit=16 << 20) -> bytes:
        self._body_read = True
        te = (self.headers.get("Transfer-Encoding") or "").lower()
        if "chunked" in te:
            out = bytearray()
            while True:
                line = self.rfile.readline(65537)
                size = int(line.split(b";")[0].strip() or b"0", 16)
                if size == 0:
                    while self.rfile.readline(65537) not in (b"\r\n", b"\n", b""):
                        pass
                    return bytes(out)
                out += self.rfile.read(size)
                self.rfile.readline(3)
                if len(out) > limit:
                    raise ValueError("request too large")
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            raise ValueError("request too large")
        return self.rfile.read(n) if n else b""

    def _drain(self):
        if getattr(self, "_body_read", False):
            return
        n = int(self.headers.get("Content-Length") or 0)
        if 0 < n <= 1 << 20:
            try:
                self.rfile.read(n)
            except OSError:
                self.close_connection = True
        elif n > 0 or "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            self.close_connection = True

    def _dispatch(self):
        self._body_read = False
        try:
            if not self.host_ok():
                return self.send_plain(403, b'{"error": "bad host"}')
            path = urllib.parse.urlsplit(self.path).path
            if path == LOCAL or path.startswith(LOCAL + "/"):
                from . import routes
                return routes.dispatch(self)
            if self.server.target is None:
                return self.send_plain(302, b"", "text/plain", {"Location": LOCAL + "/"})
            return self._forward()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True
        except Exception as e:  # noqa: BLE001 -- answered, and logged
            import traceback
            warn(f"{self.command} {self.path.split('?')[0]}: {e.__class__.__name__}: {e}", src="http")
            traceback.print_exc()
            try:
                self.send_plain(500, ('{"error": %s}' % __import__("json").dumps(f"{e.__class__.__name__}: {e}")).encode())
            except OSError:
                self.close_connection = True
        finally:
            self._drain()

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _dispatch

    # ------------------------------------------------------------------ to Warden
    def _drop_upstream(self):
        if self.upstream is not None:
            try:
                self.upstream.close()
            except Exception:   # noqa: BLE001
                pass
            self.upstream = None

    def _upstream_conn(self, ep):
        if self.upstream is None:
            chan = self.app.panel_channel(self.server.target)
            chan.settimeout(UPSTREAM_TIMEOUT)
            c = http.client.HTTPConnection("localhost", ep["port"], timeout=UPSTREAM_TIMEOUT)
            c.sock = ChannelSocket(chan)
            self.upstream = c
            self.upstream_fresh = True
        return self.upstream

    def _upstream_authority(self, ep) -> str:
        return f"localhost:{ep['port']}"

    def _request_headers(self, ep) -> list:
        """The browser's headers as Warden should see them (a browser on the server host)."""
        up = self._upstream_authority(ep)
        mine = self.server.origin
        out = []
        for k, v in self.headers.items():
            kl = k.lower()
            if kl in HOP or kl in ("host", "content-length", "accept-encoding"):
                continue
            if kl == "origin":
                v = f"http://{up}" if v.lower() == mine else v
            elif kl == "referer" and v.lower().startswith(mine + "/"):
                v = f"http://{up}" + v[len(mine):]
            elif kl == "cookie":
                kept = [p for p in _split_cookie_header(v) if not p.startswith(COOKIE + "=")]
                if not kept:
                    continue
                v = "; ".join(kept)
            out.append((k, v))
        out.append(("Host", up))
        out.append(("Accept-Encoding", "identity"))      # (an HTML page is read to add the app's script)
        return out

    def _response_headers(self, resp, ep) -> list:
        up = self._upstream_authority(ep)
        mine = self.server.origin
        out = []
        for k, v in resp.getheaders():
            kl = k.lower()
            if kl in HOP or kl in ("content-length", "date", "server"):
                continue                        # (send_response writes its own Date and Server)
            if kl == "location":
                for pre in (f"http://{up}", f"http://127.0.0.1:{ep['port']}", f"https://{up}"):
                    if v.startswith(pre):
                        v = mine + v[len(pre):]
                        break
            elif kl == "set-cookie":
                v = re.sub(r";\s*domain=[^;]*", "", v, flags=re.I)
            elif kl == "x-frame-options":
                v = "SAMEORIGIN"
            elif kl == "content-security-policy":
                v = rewrite_csp(v)
            out.append((k, v))
        return out

    def _forward(self):
        t = self.server.target
        t0 = time.time()
        path_only = self.path.split("?")[0]
        try:
            ep = self.app.panel_endpoint(t)
        except TunnelError as e:
            return self._unreachable(str(e))
        if (self.headers.get("Upgrade") or "").lower() == "websocket":
            return self._forward_upgrade(ep)
        data = self.body()
        headers = self._request_headers(ep)
        resp = None
        for attempt in (1, 2):
            try:
                conn = self._upstream_conn(ep)
            except TunnelError as e:
                return self._unreachable(str(e))
            fresh = getattr(self, "upstream_fresh", True)
            self.upstream_fresh = False
            try:
                conn.putrequest(self.command, self.path, skip_host=True, skip_accept_encoding=True)
                for k, v in headers:
                    conn.putheader(k, v)
                if data or self.command in ("POST", "PUT", "PATCH"):
                    conn.putheader("Content-Length", str(len(data)))
                conn.endheaders(data or None)
                resp = conn.getresponse()
                break
            except (OSError, http.client.HTTPException, EOFError) as e:
                self._drop_upstream()
                # a kept connection Warden closed in the meantime: once more on a new one (any method:
                # nothing reached Warden if it had closed it before reading)
                if attempt == 1 and not fresh:
                    continue
                return self._unreachable(f"Warden did not answer: {e.__class__.__name__}: {e}")
        status = resp.status
        self.server.requests += 1
        self.server.last_status, self.server.last_at = status, time.time()
        rh = self._response_headers(resp, ep)
        ctype = (resp.getheader("Content-Type") or "").lower()
        no_body = self.command == "HEAD" or status in (204, 304) or 100 <= status < 200
        try:
            if no_body:
                self.send_response(status, resp.reason)
                for k, v in rh:
                    self.send_header(k, v)
                if self.command == "HEAD" and resp.getheader("Content-Length"):
                    self.send_header("Content-Length", resp.getheader("Content-Length"))
                elif status not in (204, 304) and not 100 <= status < 200:
                    self.send_header("Content-Length", "0")
                self.end_headers()
                resp.read()
            elif ctype.startswith("text/html") and (resp.length is None or resp.length <= INJECT_MAX):
                body = resp.read(INJECT_MAX + 1)
                whole = len(body) <= INJECT_MAX
                if whole:
                    body = inject_capture(body, script_nonce(resp.getheader("Content-Security-Policy")))
                self.send_response(status, resp.reason)
                for k, v in rh:
                    self.send_header(k, v)
                if whole:
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:                           # (an over-long page: passed on as it is, as a stream)
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    while body:
                        self.wfile.write(b"%x\r\n%s\r\n" % (len(body), body))
                        body = resp.read1(65536)
                    self.wfile.write(b"0\r\n\r\n")
            elif resp.length is not None:
                self.send_response(status, resp.reason)
                for k, v in rh:
                    self.send_header(k, v)
                self.send_header("Content-Length", str(resp.length))
                self.end_headers()
                left = resp.length
                while left > 0:
                    chunk = resp.read(min(65536, left))
                    if not chunk:
                        self.close_connection = True
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            else:
                # unknown length (chunked, a stream such as the console's): passed on as it comes
                self.send_response(status, resp.reason)
                for k, v in rh:
                    self.send_header(k, v)
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                while True:
                    chunk = resp.read1(65536)
                    if not chunk:
                        break
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.write(b"0\r\n\r\n")
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self._drop_upstream()
            self.close_connection = True
            return
        finally:
            if resp is not None and resp.will_close:
                self._drop_upstream()
        dt = (time.time() - t0) * 1000
        # the log: every action, every server error, a slow answer, and a GET's status when it changes
        # (the roster asked every 2 s while signed out is one line, not one a poll)
        key = (self.command, path_only)
        seen = self.server.statuses.get(key)
        self.server.statuses[key] = status
        if (self.command != "GET" and path_only.startswith("/api/")) or status >= 500 or dt > 3000 or (status >= 400 and seen != status):
            log(f"{t['key']}: {self.command} {path_only} -> {status} ({dt:.0f} ms)", src="panel")

    def _forward_upgrade(self, ep):
        """A WebSocket (or any Upgrade): the handshake with the rewritten headers, then both ways as is."""
        t = self.server.target
        try:
            chan = self.app.panel_channel(t)
        except TunnelError as e:
            return self._unreachable(str(e))
        head = [f"{self.command} {self.path} HTTP/1.1"]
        for k, v in self._request_headers(ep):
            if k.lower() == "accept-encoding":
                continue
            head.append(f"{k}: {v}")
        head.append(f"Connection: {self.headers.get('Connection') or 'Upgrade'}")
        head.append(f"Upgrade: {self.headers.get('Upgrade')}")
        chan.sendall(("\r\n".join(head) + "\r\n\r\n").encode("latin-1"))
        buf = b""
        chan.settimeout(30)
        while b"\r\n\r\n" not in buf:
            d = chan.recv(4096)
            if not d:
                chan.close()
                return self._unreachable("Warden closed the connection during a WebSocket handshake")
            buf += d
            if len(buf) > 65536:
                chan.close()
                return self._unreachable("Warden's WebSocket answer was too long")
        hdr, rest = buf.split(b"\r\n\r\n", 1)
        self.close_connection = True
        self.wfile.write(hdr + b"\r\n\r\n")
        if rest:
            self.wfile.write(rest)
        status = hdr.split(b" ", 2)[1:2]
        log(f"{t['key']}: upgrade {self.path.split('?')[0]} -> {status[0].decode() if status else '?'}", src="panel")
        if not status or status[0] != b"101":
            chan.close()
            return
        chan.settimeout(None)
        sock = self.connection
        sock.settimeout(None)
        done = threading.Event()

        def up():
            try:
                while not done.is_set():
                    d = sock.recv(65536)
                    if not d:
                        break
                    chan.sendall(d)
            except OSError:
                pass
            finally:
                done.set()
                try:
                    chan.shutdown_write()
                except Exception:   # noqa: BLE001
                    pass

        th = threading.Thread(target=up, daemon=True)
        th.start()
        try:
            while not done.is_set():
                d = chan.recv(65536)
                if not d:
                    break
                sock.sendall(d)
        except OSError:
            pass
        finally:
            done.set()
            chan.close()
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def _unreachable(self, why: str):
        self.server.errors += 1
        t = self.server.target
        warn(f"{t['key'] if t else 'home'}: {self.command} {self.path.split('?')[0]}: {why}", src="panel")
        path = urllib.parse.urlsplit(self.path).path
        if path.startswith("/api/") or "json" in (self.headers.get("Accept") or ""):
            body = __import__("json").dumps({"error": why, "reachable": False, "o77w": True}).encode()
            return self.send_plain(502, body, "application/json")
        import html
        page = f"""<!doctype html><meta charset="utf-8"><title>Warden not reachable</title>
<style>body{{background:#05080d;color:#dfe8f0;font:15px/1.5 'Segoe UI',system-ui,sans-serif;margin:0;display:grid;place-items:center;height:100vh}}
main{{max-width:620px;padding:28px;border:1px solid #1d3a4a;border-radius:10px;background:#0a1018}}
h1{{font-size:18px;color:#ff5577;letter-spacing:.06em;margin:0 0 10px}}code{{color:#9ef3ed}}a{{color:#47d4ff}}</style>
<main><h1>WARDEN NOT REACHABLE</h1><p>{html.escape(why)}</p>
<p>The app tries again on every page you open. <a href="{LOCAL}/">Back to the app</a></p></main>"""
        return self.send_plain(502, page.encode("utf-8"), "text/html; charset=utf-8")


def bind_panel(app, target, token, slug, port: int) -> PanelServer:
    """A listener on 127.0.0.1:port (or the next free port when it is taken)."""
    last = None
    for p in ([port] if port else []) + [0]:
        try:
            return PanelServer(("127.0.0.1", p), app, target, token, slug)
        except OSError as e:
            last = e
            if p:
                warn(f"{slug}: port {p} is taken ({e}); using another", src="http")
    raise OSError(f"no port for {slug}: {last}")

"""A stand-in for an Open77 server's Warden panel, for the Warden app's tests: a sign-in page, a
panel page whose script calls the API with an anti-forgery header, the players' roster (players
walking about Night City, one in a car, one dead, one frozen) and the Players tab's actions, with
the checks a real panel makes: a session cookie, the anti-forgery header, a same-origin Origin,
and (strict) the Host an ``ssh -L`` tunnel would give.  Its pages may not be framed (X-Frame-Options
DENY, frame-ancestors 'none'), it streams its console (server-sent events) and answers a WebSocket.

    w = FakeWarden(strict_host=True).start()      # w.port, w.log (every request), w.actions
"""
from __future__ import annotations

import hashlib
import json
import math
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

USER, PASSWORD = "owner", "correct horse"
CSRF_HEADER = "X-Warden-Csrf"

PAGE_LOGIN = """<!doctype html><html><head><meta charset="utf-8"><title>Warden - sign in</title>
<script src="/app.js"></script></head><body><h1>Warden</h1>
<form id="f"><input name="u" id="u"><input name="p" id="p" type="password"><button id="go">Sign in</button></form>
<p id="msg"></p></body></html>"""
# Warden's own layout (Open77.Server 2.50's panel): the body never scrolls (body { overflow: hidden }, the panel's
# views scroll themselves), the app fills the window, and the first-run page -- the owner account and the setup
# PIN from the server's log -- is a column centred in a box at least as tall as the window (.gate-wrap): in a
# frame shorter than the form, its end (the confirm-password box, the button) is cut off with no way to scroll
WARDEN_CSS = """html,body{height:100%}
body{margin:0;font:15px/1.55 sans-serif;background:#0b1118;color:#e6edf3;overflow:hidden}
[hidden]{display:none!important}
.gate-wrap{min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:32px 20px;
  box-sizing:border-box;position:relative}
.gate{width:100%;max-width:430px;position:relative}
.gate .lockup{margin:0 0 16px;padding:19px 22px;background:#e8eff1;color:#07101b}
.gate .lockup .lg{font-size:26px;font-weight:700;font-style:italic}.gate .lockup .tag{font-size:10px;letter-spacing:.3em;margin-top:5px}
.gate h2{font-size:20px;margin:0 0 3px;text-transform:uppercase}.gate .sub{font-size:13px;margin:0 0 6px;color:#9fb0bf}
.card{border:1px solid #233544;border-radius:6px;overflow:hidden}.card-b{padding:20px 22px}
.field{margin:0 0 14px}.field label{display:block;font-size:12px;margin:0 0 5px}
.field input{display:block;width:100%;box-sizing:border-box;height:38px}.field .hint{font-size:11.5px;margin-top:4px;color:#7d8d9b}
.gate-nav{display:flex;justify-content:space-between;gap:10px;margin-top:18px}.gate-nav button{height:38px;padding:0 18px}
.gate-foot{margin-top:18px;text-align:center;font-size:10px;letter-spacing:.14em;text-transform:uppercase}
.app{height:100vh;display:grid;grid-template-rows:auto 1fr}.top{padding:10px 24px;border-bottom:1px solid #233544}
.view{overflow-y:auto;padding:22px 24px 40px;min-width:0}"""
PAGE_SETUP = """<!doctype html><html><head><meta charset="utf-8"><title>Warden - set up</title>
<link rel="stylesheet" href="/warden.css"><script src="/app.js"></script></head><body>
<div id="gate" class="gate-wrap"><div class="gate"><div class="lockup"><div class="lg">WARDEN//77</div><div class="tag">server ops</div></div>
<h2>Create the owner</h2><p class="sub">Enter the one-time PIN printed in the server log, then choose your credentials.</p>
<form id="s"><div class="card"><div class="card-b">
<div class="field"><label for="spin">Setup PIN</label><input id="spin" inputmode="numeric" placeholder="8 digits from the server log">
<div class="hint">Look for "Warden setup PIN" in the terminal where the server is running.</div></div>
<div class="field"><label for="su">Username</label><input id="su"><div class="hint">32 characters: letters, digits, _ - .</div></div>
<div class="field"><label for="sp">Password</label><input id="sp" type="password"><div class="hint">At least 8 characters.</div></div>
<div class="field"><label for="sp2">Confirm password</label><input id="sp2" type="password"></div>
<div class="err" id="smsg"></div></div></div>
<div class="gate-nav"><button type="button" disabled>Back</button><button id="sgo">Create the owner account</button></div></form>
<div class="gate-foot">OPEN//77 dedicated server</div></div></div>
<div id="app" class="app" hidden></div></body></html>"""
PAGE_PANEL = """<!doctype html><html><head><meta charset="utf-8"><title>Warden</title>
<link rel="stylesheet" href="/warden.css"><script src="/app.js"></script></head><body><div id="gate" class="gate-wrap" hidden></div>
<div id="app" class="app"><div class="top">WARDEN//77</div><main class="view"><h1>Warden: Players</h1><table id="t"></table></main></div>
</body></html>"""
APP_JS = r"""
(function(){
  async function csrf(){ const r = await fetch('/api/session', {credentials:'same-origin'}); const j = await r.json(); return j.csrf; }
  document.addEventListener('DOMContentLoaded', async function(){
    const su = document.getElementById('s');
    if (su) {
      su.addEventListener('submit', async function(e){ e.preventDefault();
        const r = await fetch('/api/setup', {method:'POST', headers:{'Content-Type':'application/json'}, credentials:'same-origin',
          body: JSON.stringify({username: document.getElementById('su').value, password: document.getElementById('sp').value,
                                confirm: document.getElementById('sp2').value, pin: document.getElementById('spin').value})});
        if (r.ok) location.href = '/'; else document.getElementById('smsg').textContent = 'refused';
      });
      return;
    }
    const f = document.getElementById('f');
    if (f) {
      f.addEventListener('submit', async function(e){ e.preventDefault();
        const r = await fetch('/api/login', {method:'POST', headers:{'Content-Type':'application/json'}, credentials:'same-origin',
          body: JSON.stringify({username: document.getElementById('u').value, password: document.getElementById('p').value})});
        if (r.ok) location.href = '/'; else document.getElementById('msg').textContent = 'refused';
      });
      return;
    }
    const token = await csrf();
    window.__wardenHeal = async function(id){
      return fetch('/api/players/' + id + '/heal', {method:'POST', credentials:'same-origin',
        headers:{'Content-Type':'application/json', 'X-Warden-Csrf': token}, body: '{}'});
    };
    window.__wardenTeleport = async function(id, x, y, z){
      return fetch('/api/players/' + id + '/teleport', {method:'POST', credentials:'same-origin',
        headers:{'Content-Type':'application/json', 'X-Warden-Csrf': token}, body: JSON.stringify({x:x, y:y, z:z, leaveVehicle:false})});
    };
    async function tick(){
      const r = await fetch('/api/players', {credentials:'same-origin'});
      if (r.ok) { const j = await r.json(); document.getElementById('t').textContent = j.players.map(p => p.name).join(', '); }
    }
    tick(); setInterval(tick, 2000);
  });
})();
"""

# Night City spots (game metres: x east, y north, z up) around Corpo Plaza / City Center
CENTRE = (-1436.0, -8.0)


class Player:
    def __init__(self, pid, name, centre, radius, period, z=12.0, states=(), ping=40, hp=100, armour=50):
        self.id, self.name = pid, name
        self.centre, self.radius, self.period, self.z = centre, radius, period, z
        self.states = set(states)
        self.ping, self.hp, self.armour = ping, hp, armour
        self.override = None              # (x, y, z) after a move
        self.t0 = time.time()

    def pos(self, now):
        if self.override is not None:
            return self.override
        if "dead" in self.states or "frozen" in self.states or not self.period:
            a = 0.0
        else:
            a = (now - self.t0) / self.period * 2 * math.pi
        cx, cy = self.centre
        return (cx + self.radius * math.cos(a), cy + self.radius * math.sin(a), self.z)

    def heading(self, now):
        a = (now - self.t0) / self.period * 2 * math.pi if self.period else 0.0
        return (math.degrees(a) + 180.0) % 360.0 - 180.0


def default_players():
    cx, cy = CENTRE
    return [
        Player(3, "Valerie", (cx, cy), 40.0, 40.0, ping=38),
        Player(5, "Jackie", (cx + 60, cy - 30), 25.0, 30.0, ping=61, hp=72),
        Player(8, "Rogue", (cx - 80, cy + 50), 160.0, 25.0, states=("vehicle",), ping=24),       # driving round
        Player(11, "Takemura", (cx + 20, cy + 90), 0.0, 0.0, states=("dead",), ping=90, hp=0, armour=0),
        Player(13, "Panam", (cx - 30, cy - 70), 0.0, 0.0, states=("frozen",), ping=55),
    ]


class FakeWarden:
    def __init__(self, strict_host=True, players=None, setup_pin=None):
        self.strict_host = strict_host
        self.setup_pin = setup_pin         # (first run: no operator until the owner is made with this PIN)
        self.owner = None
        self.players = players if players is not None else default_players()
        self.sessions = {}                # cookie -> csrf
        self.log = []                     # (method, path, host, origin, status)
        self.actions = []                 # (path, body)
        self.lock = threading.Lock()
        self.srv = None
        self.port = None

    def start(self):
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _send(self, code, body=b"", ctype="application/json", headers=None):
                if isinstance(body, (dict, list)):
                    body = json.dumps(body).encode()
                elif isinstance(body, str):
                    body = body.encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)
                with outer.lock:
                    outer.log.append((self.command, self.path, self.headers.get("Host"), self.headers.get("Origin"), code))

            def _session(self):
                for part in (self.headers.get("Cookie") or "").split(";"):
                    k, _, v = part.strip().partition("=")
                    if k == "warden_session" and v in outer.sessions:
                        return v
                return None

            def _host_ok(self):
                return not outer.strict_host or self.headers.get("Host") == f"localhost:{outer.port}"

            def _origin_ok(self):
                o = self.headers.get("Origin")
                return o is None or o == "http://" + (self.headers.get("Host") or "")

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                try:
                    return json.loads(raw or b"{}")
                except ValueError:
                    return None

            def do_GET(self):
                if not self._host_ok():
                    return self._send(421, {"error": "misdirected request (host)"})
                path = urlsplit(self.path).path
                page_headers = {"X-Frame-Options": "DENY",
                                "Content-Security-Policy": "default-src 'self'; script-src 'self'; frame-ancestors 'none'"}
                if path == "/":
                    if self._session():
                        return self._send(200, PAGE_PANEL, "text/html; charset=utf-8", page_headers)
                    if outer.setup_pin and not outer.owner:
                        return self._send(200, PAGE_SETUP, "text/html; charset=utf-8", page_headers)
                    return self._send(200, PAGE_LOGIN, "text/html; charset=utf-8", page_headers)
                if path == "/app.js":
                    return self._send(200, APP_JS, "text/javascript")
                if path == "/warden.css":
                    return self._send(200, WARDEN_CSS, "text/css")
                if path == "/old":
                    return self._send(302, b"", "text/plain", {"Location": f"http://localhost:{outer.port}/"})
                if path == "/api/session":
                    s = self._session()
                    return self._send(200 if s else 401, {"signedIn": bool(s), "user": USER if s else None,
                                                           "csrf": outer.sessions.get(s) if s else None})
                if path == "/api/players":
                    if not self._session():
                        return self._send(401, {"error": "not signed in"})
                    now = time.time()
                    out = []
                    for p in outer.players:
                        x, y, z = p.pos(now)
                        out.append({"id": p.id, "name": p.name, "identity": hashlib.sha1(p.name.encode()).hexdigest()[:32],
                                    "connectedAtUtc": "2026-10-22T16:00:00Z", "pingMs": p.ping,
                                    "health": {"current": p.hp, "max": 100}, "armor": p.armour,
                                    "states": sorted(p.states), "bucket": 0,
                                    "position": {"x": round(x, 2), "y": round(y, 2), "z": round(z, 2)}, "positionAgeMs": 180,
                                    "heading": round(p.heading(now), 1)})
                    return self._send(200, {"ok": True, "players": out})
                if path == "/api/console/stream":
                    if not self._session():
                        return self._send(401, {"error": "not signed in"})
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    for i in range(3):
                        chunk = f"data: {json.dumps({'seq': i, 'message': f'line {i}'})}\n\n".encode()
                        self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                        self.wfile.flush()
                        time.sleep(0.3)
                    self.wfile.write(b"0\r\n\r\n")
                    with outer.lock:
                        outer.log.append(("GET", self.path, self.headers.get("Host"), None, 200))
                    return
                if path == "/ws" and (self.headers.get("Upgrade") or "").lower() == "websocket":
                    self.send_response(101, "Switching Protocols")
                    self.send_header("Upgrade", "websocket")
                    self.send_header("Connection", "Upgrade")
                    self.end_headers()
                    with outer.lock:
                        outer.log.append(("GET", self.path, self.headers.get("Host"), self.headers.get("Origin"), 101))
                    self.close_connection = True
                    # an echo (raw bytes, enough to see the pipe both ways)
                    while True:
                        d = self.connection.recv(4096)
                        if not d:
                            break
                        self.connection.sendall(b"echo:" + d)
                    return
                return self._send(404, {"error": "no route"})

            do_HEAD = do_GET

            def do_POST(self):
                if not self._host_ok():
                    return self._send(421, {"error": "misdirected request (host)"})
                path = urlsplit(self.path).path
                body = self._body()
                if not self._origin_ok():
                    return self._send(403, {"error": "cross-origin request refused"})
                if path == "/__fake/z":                     # (the tests: stand the players on the ground)
                    for p in outer.players:
                        if str(p.id) in (body or {}):
                            p.z = float(body[str(p.id)])
                    return self._send(200, {"ok": True})
                if path == "/__fake/reset":                 # (the tests: everybody back as they joined)
                    outer.players = default_players()
                    with outer.lock:
                        outer.actions.clear()
                    return self._send(200, {"ok": True})
                if path == "/api/setup":
                    b = body or {}
                    if not outer.setup_pin or outer.owner:
                        return self._send(409, {"ok": False, "error": "already set up"})
                    if b.get("pin") != outer.setup_pin or not b.get("username") or b.get("password") != b.get("confirm"):
                        return self._send(400, {"ok": False, "error": "check the PIN and the passwords"})
                    outer.owner = b["username"]
                    sid, csrf = secrets.token_hex(16), secrets.token_hex(8)
                    outer.sessions[sid] = csrf
                    return self._send(200, {"ok": True}, headers={"Set-Cookie": f"warden_session={sid}; Path=/; HttpOnly; SameSite=Strict"})
                if path == "/api/login":
                    if body and body.get("username") == USER and body.get("password") == PASSWORD:
                        sid, csrf = secrets.token_hex(16), secrets.token_hex(8)
                        outer.sessions[sid] = csrf
                        return self._send(200, {"ok": True}, headers={"Set-Cookie": f"warden_session={sid}; Path=/; HttpOnly; SameSite=Strict; Domain=localhost"})
                    return self._send(401, {"ok": False, "error": "bad credentials"})
                s = self._session()
                if not s:
                    return self._send(401, {"error": "not signed in"})
                if self.headers.get(CSRF_HEADER) != outer.sessions[s]:
                    return self._send(403, {"error": "anti-forgery token missing or wrong"})
                parts = path.strip("/").split("/")
                if len(parts) == 4 and parts[:2] == ["api", "players"]:
                    pid, act = int(parts[2]), parts[3]
                    p = next((q for q in outer.players if q.id == pid), None)
                    if p is None:
                        return self._send(404, {"ok": False, "error": "no such player"})
                    with outer.lock:
                        outer.actions.append((path, body))
                    if act == "heal":
                        was_dead = "dead" in p.states
                        p.states.discard("dead")
                        p.hp = 100
                        return self._send(200, {"ok": True, "outcome": "revived" if was_dead else "healed"})
                    if act == "freeze":
                        p.states.add("frozen")
                        return self._send(200, {"ok": True, "outcome": "frozen"})
                    if act == "unfreeze":
                        p.states.discard("frozen")
                        return self._send(200, {"ok": True, "outcome": "released"})
                    if act == "kick":
                        if not (body or {}).get("reason"):
                            return self._send(400, {"ok": False, "error": "reason is required"})
                        outer.players = [q for q in outer.players if q.id != pid]
                        return self._send(200, {"ok": True, "outcome": "kicked"})
                    if act == "warn":
                        if not (body or {}).get("message"):
                            return self._send(400, {"ok": False, "error": "message is required"})
                        return self._send(200, {"ok": True, "outcome": "sent"})
                    if act == "teleport":
                        b = body or {}
                        if "vehicle" in p.states and not b.get("leaveVehicle"):
                            return self._send(200, {"ok": False, "outcome": "player_in_vehicle", "error": "player_in_vehicle"})
                        if "dead" in p.states:
                            return self._send(200, {"ok": False, "outcome": "player_not_alive", "error": "player_not_alive"})
                        t = b.get("targetPlayerId")
                        if t is not None:
                            q = next((o for o in outer.players if o.id == int(t)), None)
                            if q is None:
                                return self._send(404, {"ok": False, "error": "no such target"})
                            x, y, z = q.pos(time.time())
                            p.override = (x + 1.5, y, z)
                        elif all(k in b for k in ("x", "y", "z")):
                            p.override = (float(b["x"]), float(b["y"]), float(b["z"]))
                        else:
                            return self._send(400, {"ok": False, "error": "x, y, z or targetPlayerId"})
                        p.states.discard("vehicle")
                        return self._send(200, {"ok": True, "outcome": "arrived"})
                return self._send(404, {"error": "no route"})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return self

    def stop(self):
        if self.srv:
            self.srv.shutdown()
            self.srv.server_close()

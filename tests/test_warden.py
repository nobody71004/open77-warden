"""Open77 Warden (fivem2open77/warden): a server's Warden reached through a real SSH server (OpenSSH
on 127.0.0.1, its direct-tcpip forwarding) and seen as a site of its own on this machine, the
way Warden itself sees the requests (Host and Origin as through ``ssh -L``), what the app changes
on the way back (redirects, cookies' Domain, framing, its script in the pages), streams and
WebSockets through it, the app's own API beside it, and the roster/actions contract the page uses."""
import http.client
import json
import os
import socket
import sys
import tempfile
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import sshd_fixture  # noqa: E402
from fake_warden import PASSWORD, USER, FakeWarden  # noqa: E402

from fivem2open77.warden import config as C  # noqa: E402
from fivem2open77.warden import tunnel as T  # noqa: E402
from fivem2open77.warden.proxy import inject_capture, rewrite_csp, script_nonce  # noqa: E402

needs_sshd = pytest.mark.skipif(not sshd_fixture.available(), reason="needs sshd and root")


def req(port, method, path, host, headers=None, body=None, timeout=20):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    c.putheader("Host", host)
    for k, v in (headers or {}).items():
        c.putheader(k, v)
    data = json.dumps(body).encode() if isinstance(body, (dict, list)) else (body or b"")
    if data or method in ("POST", "PUT", "PATCH"):
        c.putheader("Content-Type", "application/json")
        c.putheader("Content-Length", str(len(data)))
    c.endheaders(data or None)
    r = c.getresponse()
    out = r.read()
    c.close()
    return r, out


def test_jsonc_and_listen_urls():
    cfg = C  # (imported for the module's own checks below)
    assert cfg.slug_of("srv", "open77-xbuniverse", set()) == "open77-xbuniverse"
    taken = {"open77-test"}
    assert C.slug_of("vps2", "open77-test", taken) == "open77-test-vps2"
    text = """{ // a comment
      "identity": {"name": "XyberRP", "description": "x"},  /* and another */
      "warden": {"enabled": true, "listenUrl": "http://0.0.0.0:11780",},
      "rcon": {"enabled": false}, "note": "a // not a comment",
    }"""
    info = T.read_config(text)
    assert info["name"] == "XyberRP" and info["warden"] == {"enabled": True, "listenUrl": "http://0.0.0.0:11780"}
    assert T.warden_endpoint("http://0.0.0.0:11780") == ("127.0.0.1", 11780, "http")
    assert T.warden_endpoint("http://[::]:9000") == ("127.0.0.1", 9000, "http")
    assert T.warden_endpoint("http://10.0.0.5:11780")[:2] == ("10.0.0.5", 11780)
    with pytest.raises(T.TunnelError):
        T.warden_endpoint("tcp://x:1")


def test_page_rewrites():
    assert rewrite_csp("default-src 'self'; frame-ancestors 'none'") == "default-src 'self'; frame-ancestors 'self'"
    assert script_nonce("script-src 'self' 'nonce-abc123'") == "abc123"
    out = inject_capture(b"<!doctype html><html><head><title>x</title></head></html>", "n1")
    assert b'<head><script src="/__o77w/capture.js" nonce="n1"></script><title>' in out
    assert inject_capture(b"<p>bare</p>", None).startswith(b'<script src="/__o77w/capture.js"></script>')


class Rig:
    """sshd + a fake Warden behind it + the app serving, with its home and panel addresses."""

    def __init__(self, strict=True, warden_enabled=True):
        self.strict, self.enabled = strict, warden_enabled

    def __enter__(self):
        self.tmp = tempfile.mkdtemp(prefix="o77w-")
        self.sshd = sshd_fixture.Sshd().__enter__()
        self.warden = FakeWarden(strict_host=self.strict).start()
        root = os.path.join(self.tmp, "srv")
        os.makedirs(os.path.join(root, "open77-test"))
        with open(os.path.join(root, "open77-test", "server.jsonc"), "w") as f:
            f.write("{\n  // the test server\n  \"identity\": {\"name\": \"Test City\"},\n"
                    f"  \"warden\": {{\"enabled\": {str(self.enabled).lower()}, \"listenUrl\": \"http://0.0.0.0:{self.warden.port}\"}},\n}}\n")
        ws = os.path.join(self.tmp, "ws")
        os.makedirs(ws)
        with open(os.path.join(ws, "settings.json"), "w") as f:
            json.dump({"studio": {"use": False}, "servers": [{
                "id": "test", "label": "Test VPS", "host": "127.0.0.1", "port": self.sshd.port, "user": self.sshd.user,
                "auth": "key", "key_file": self.sshd.key, "servers_root": root, "names": ["open77-test"]}]}, f)
        from fivem2open77.warden.server import serve
        self.token = "test-token-0123456789"
        got = {}
        ready = threading.Event()

        def on_ready(app, home):
            got.update(app=app, home=home)
            ready.set()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=serve, kwargs={"port": 0, "workspace": ws, "token": self.token, "on_ready": on_ready,
                                                             "stop": self.stop}, daemon=True)
        self.thread.start()
        assert ready.wait(30), "the app did not start"
        self.app, self.home = got["app"], got["home"]
        t = self.app.state()["targets"][0]
        self.target = t
        self.port, self.host = t["port"], f"{t['slug']}.localhost:{t['port']}"
        self.cookie = f"o77w={self.token}"
        return self

    def __exit__(self, *a):
        self.stop.set()
        self.thread.join(10)
        self.warden.stop()
        self.sshd.__exit__(*a)


@needs_sshd
def test_the_panel_through_ssh_as_warden_sees_it():
    with Rig(strict=True) as rig:
        port, host = rig.port, rig.host
        # a name this address does not answer to: refused (a page elsewhere cannot rebind to it)
        r, _ = req(port, "GET", "/", "evil.example:80")
        assert r.status == 403
        # the sign-in page: Warden's own, with the app's script in it, and framable by its own origin only
        r, body = req(port, "GET", "/", host)
        assert r.status == 200 and b"Warden - sign in" in body
        assert b'<script src="/__o77w/capture.js"></script>' in body
        assert r.getheader("X-Frame-Options") == "SAMEORIGIN"
        assert "frame-ancestors 'self'" in r.getheader("Content-Security-Policy")
        # Warden saw the request as through ssh -L: Host localhost:<its port>
        assert rig.warden.log[-1][2] == f"localhost:{rig.warden.port}"
        # sign in: the Origin is Warden's own (it refuses a foreign one), the cookie keeps no Domain
        r, body = req(port, "POST", "/api/login", host, {"Origin": f"http://{host}"}, {"username": USER, "password": PASSWORD})
        assert r.status == 200, body
        sc = r.getheader("Set-Cookie")
        assert "warden_session=" in sc and "domain" not in sc.lower()
        assert rig.warden.log[-1][3] == f"http://localhost:{rig.warden.port}"
        cookie = sc.split(";")[0]
        r, body = req(port, "POST", "/api/login", host, {"Origin": "http://evil.example"}, {"username": USER, "password": PASSWORD})
        assert r.status == 403                                    # (a foreign origin stays foreign)
        # the roster, with the operator's cookie (and the app's own cookie not passed to Warden)
        r, body = req(port, "GET", "/api/players", host, {"Cookie": f"{rig.cookie}; {cookie}"})
        assert r.status == 200
        players = json.loads(body)["players"]
        assert {p["name"] for p in players} >= {"Valerie", "Rogue", "Takemura"}
        # an action with the anti-forgery header the panel uses
        r, body = req(port, "GET", "/api/session", host, {"Cookie": cookie})
        csrf = json.loads(body)["csrf"]
        r, body = req(port, "POST", "/api/players/11/heal", host, {"Cookie": cookie, "Origin": f"http://{host}", "X-Warden-Csrf": csrf}, {})
        assert r.status == 200 and json.loads(body)["outcome"] == "revived"
        r, body = req(port, "POST", "/api/players/3/heal", host, {"Cookie": cookie, "Origin": f"http://{host}"}, {})
        assert r.status == 403                                    # (no anti-forgery header: Warden refuses)
        # a redirect to Warden's own address comes back to this one
        r, _ = req(port, "GET", "/old", host)
        assert r.status == 302 and r.getheader("Location") == f"http://{host}/"
        # the console stream arrives as it comes
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
        c.putrequest("GET", "/api/console/stream", skip_host=True)
        c.putheader("Host", host)
        c.putheader("Cookie", cookie)
        c.endheaders()
        resp = c.getresponse()
        assert resp.status == 200 and resp.getheader("Content-Type") == "text/event-stream"
        t0 = time.time()
        first = resp.read1(4096)
        assert b"line 0" in first and time.time() - t0 < 0.9      # (before the stream ends ~0.9 s later)
        rest = resp.read()
        assert b"line 2" in first + rest
        c.close()
        # a WebSocket: the handshake through, then both ways as is
        s = socket.create_connection(("127.0.0.1", port), timeout=10)
        s.sendall((f"GET /ws HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                   f"Origin: http://{host}\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            head += s.recv(1024)
        assert head.startswith(b"HTTP/1.1 101")
        s.sendall(b"ping")
        assert s.recv(1024) == b"echo:ping"
        s.close()
        assert rig.warden.log[-1][3] == f"http://localhost:{rig.warden.port}"


@needs_sshd
def test_the_apps_own_pages_and_api_beside_the_panel():
    with Rig() as rig:
        port, host = rig.port, rig.host
        r, _ = req(port, "GET", "/__o77w/api/state", host)
        assert r.status == 401                                   # (not signed in to the app)
        r, _ = req(port, "GET", f"/__o77w/auth?token={rig.token}&to=/__o77w/", host)
        assert r.status == 302 and "o77w=" in r.getheader("Set-Cookie") and "Path=/__o77w" in r.getheader("Set-Cookie")
        r, body = req(port, "GET", "/__o77w/api/state", host, {"Cookie": rig.cookie})
        st = json.loads(body)
        assert r.status == 200 and st["here"] == "test/open77-test" and st["targets"][0]["url"].startswith(f"http://{host}/__o77w/")
        r, body = req(port, "POST", "/__o77w/api/targets/test%2Fopen77-test/check", host, {"Cookie": rig.cookie, "Origin": f"http://{host}"})
        info = json.loads(body)
        assert info["display"] == "Test City" and info["warden"]["enabled"] and info["ssh"]["connected"]
        # a cross-site write to the app's API is refused
        r, _ = req(port, "POST", "/__o77w/api/log", host, {"Cookie": rig.cookie, "Origin": "http://evil.example"}, {"text": "x"})
        assert r.status == 403
        # the pages, the Studio's 3D city modules, the script Warden's pages get
        for path, kind in (("/__o77w/", "text/html"), ("/__o77w/js/panel.js", "javascript"), ("/__o77w/studio/js/pages/city3d.js", "javascript"),
                           ("/__o77w/studio/js/pages/cityplayers.js", "javascript"), ("/__o77w/capture.js", "javascript"),
                           ("/__o77w/studio/vendor/three/three.module.min.js", "javascript")):
            r, body = req(port, "GET", path, host)
            assert r.status == 200 and kind in r.getheader("Content-Type"), path
        r, _ = req(port, "GET", "/__o77w/../server.py", host)
        assert r.status == 404
        # the home address: the app's pages only, and "go" gives a panel's signed-in address
        hp = rig.home.port
        r, _ = req(hp, "GET", "/", f"warden.localhost:{hp}")
        assert r.status == 302 and r.getheader("Location") == "/__o77w/"
        r, body = req(hp, "GET", "/__o77w/api/go/test%2Fopen77-test", f"warden.localhost:{hp}", {"Cookie": rig.cookie})
        go = json.loads(body)
        assert go["url"].startswith(f"http://{host}/__o77w/auth?token=")


@needs_sshd
def test_warden_off_or_down_is_said_plainly():
    with Rig(warden_enabled=False) as rig:
        r, body = req(rig.port, "GET", "/api/players", rig.host, {"Accept": "application/json"})
        j = json.loads(body)
        assert r.status == 502 and j["o77w"] and "Warden is off on this server" in j["error"]
        r, body = req(rig.port, "GET", "/", rig.host)
        assert r.status == 502 and b"WARDEN NOT REACHABLE" in body
    with Rig() as rig:
        rig.warden.stop()
        r, body = req(rig.port, "GET", "/api/players", rig.host, {"Accept": "application/json"})
        j = json.loads(body)
        assert r.status == 502 and "nothing answers" in j["error"], j


@needs_sshd
def test_the_city_is_read_into_the_apps_own_cache():
    """The 3D city through the app's API (the Studio's WorldOps on the app's own workspace): its status,
    a build job (a stand-in for the game), then the files the map loads."""
    import shutil

    import test_citymap as TC
    from fivem2open77.studio.worldops import WorldOps
    from fivem2open77.world import citymap as CM
    src = tempfile.mkdtemp(prefix="o77w-city-")

    def fake_builder(out_dir, job):
        raw, glb, js = TC._fixture(src)
        meta, data = CM.assemble(raw, glb, js, log=job.log, buf_dir=os.path.join(src, "buf"))
        return CM._save(out_dir, meta, data, None, log=job.log)

    with Rig() as rig:
        hp = rig.home.port
        host = f"warden.localhost:{hp}"
        h = {"Cookie": rig.cookie}
        r, body = req(hp, "GET", "/__o77w/api/citymap", host, h)
        st = json.loads(body)
        assert r.status == 200 and st["ready"] is False
        WorldOps.citymap_builder = staticmethod(fake_builder)
        try:
            r, body = req(hp, "POST", "/__o77w/api/citymap/build", host, dict(h, Origin=f"http://{host}"), {})
            job = json.loads(body)
            assert r.status == 200 and job["kind"] == "citymap", body
            for _ in range(200):
                r, body = req(hp, "GET", f"/__o77w/api/jobs/{job['id']}", host, h)
                j = json.loads(body)
                if j["state"] not in ("queued", "running"):
                    break
                time.sleep(0.05)
            assert j["state"] == "done", j
        finally:
            WorldOps.citymap_builder = None
            shutil.rmtree(src, ignore_errors=True)
        r, body = req(hp, "GET", "/__o77w/api/citymap", host, h)
        st = json.loads(body)
        assert st["ready"] and st["stats"]["buildings"] == 2
        assert os.path.isfile(os.path.join(rig.app.workspace, "citymap", "citymap.bin"))      # the app's own cache
        r, body = req(hp, "GET", f"/__o77w/api/citymap/citymap.json?k={st['key']}", host, h)
        meta = json.loads(body)
        assert r.status == 200 and meta["layers"]["terrain"]["triangles"] == 2
        r, body = req(hp, "GET", f"/__o77w/api/citymap/citymap.bin?k={st['key']}", host, h)
        assert r.status == 200 and len(body) == meta["bytes"]

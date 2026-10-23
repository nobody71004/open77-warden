"""Open77 Warden's server page in a real browser (Playwright + Chromium): the app reaches a fake
Warden through a real sshd, the operator signs in inside the panel, the players stand on a small
synthetic city as holograms, and the map's own actions go to Warden with the operator's sign-in and
the anti-forgery proof the panel itself uses -- revive, thaw/freeze, a move to a spot clicked on
the map, a driver refused and then taken out of the car, a bring, a warning, a kick; follow; a
click on a hologram; an anti-forgery refusal explained, then cured by one action in the panel.

Opt-in like the Studio's page test: needs root + OpenSSH and Playwright's Chromium (software GL
here: the real meshes are off, a frame takes about a second).  O77W_PAGE_SHOTS=<dir> keeps a
screenshot."""
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

import sshd_fixture  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:                                   # pragma: no cover
    sync_playwright = None

pytestmark = pytest.mark.skipif(sync_playwright is None or not sshd_fixture.available(),
                                reason="needs Playwright's Chromium and root + OpenSSH")


def _city(ws):
    """The small synthetic 3D city of test_citymap (a 100 m terrain square, roads, two buildings)."""
    import test_citymap as TC
    from fivem2open77.world import citymap as CM
    root = tempfile.mkdtemp(prefix="o77w-city-")
    try:
        raw, glb, js = TC._fixture(root)
        meta, data = CM.assemble(raw, glb, js, log=lambda *a: None, buf_dir=os.path.join(root, "buf"))
        CM._save(CM.cache_dir(ws), meta, data, None, log=lambda *a: None)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _players():
    from fake_warden import Player
    z = 9.0                                    # (the synthetic terrain: 8 to 10 m)
    return [Player(3, "Valerie", (50, 50), 15.0, 40.0, z=z, ping=38),
            Player(5, "Jackie", (70, 30), 8.0, 30.0, z=z, ping=61, hp=72),
            Player(8, "Rogue", (30, 70), 10.0, 25.0, z=z, states=("vehicle",), ping=24),
            Player(11, "Takemura", (60, 75), 0.0, 0.0, z=z, states=("dead",), ping=90, hp=0, armour=0),
            Player(13, "Panam", (25, 25), 0.0, 0.0, z=z, states=("frozen",), ping=55)]


class Rig:
    def __init__(self, setup_pin=None):
        self.setup_pin = setup_pin

    def __enter__(self):
        from fake_warden import FakeWarden
        from fivem2open77.warden.server import serve
        self.tmp = tempfile.mkdtemp(prefix="o77w-ui-")
        self.sshd = sshd_fixture.Sshd().__enter__()
        self.warden = FakeWarden(strict_host=True, players=_players(), setup_pin=self.setup_pin).start()
        root = os.path.join(self.tmp, "srv")
        os.makedirs(os.path.join(root, "open77-ui"))
        with open(os.path.join(root, "open77-ui", "server.jsonc"), "w") as f:
            f.write('{ "identity": {"name": "UI City"}, "warden": {"enabled": true, "listenUrl": "http://0.0.0.0:%d"} }' % self.warden.port)
        ws = os.path.join(self.tmp, "ws")
        os.makedirs(ws)
        _city(ws)
        with open(os.path.join(ws, "settings.json"), "w") as f:
            json.dump({"studio": {"use": False}, "servers": [{
                "id": "ui", "label": "UI VPS", "host": "127.0.0.1", "port": self.sshd.port, "user": self.sshd.user,
                "auth": "key", "key_file": self.sshd.key, "servers_root": root, "names": ["open77-ui"]}]}, f)
        self.token = "ui-token-0123456789abcdef"
        got, ready = {}, threading.Event()

        def on_ready(app, home):
            # (no game on this machine: the synthetic city is all there is)
            app.toolbox = lambda refresh=False: SimpleNamespace(game_dir=None, wolvenkit=None, games=[], dotnet=None)
            got.update(app=app, home=home)
            ready.set()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=serve, kwargs={"port": 0, "workspace": ws, "token": self.token,
                                                             "on_ready": on_ready, "stop": self.stop}, daemon=True)
        self.thread.start()
        assert ready.wait(30), "the app did not start"
        self.app = got["app"]
        t = self.app.state()["targets"][0]
        self.url = f"{t['origin']}/__o77w/auth?token={self.token}&to=/__o77w/"
        return self

    def __exit__(self, *a):
        self.stop.set()
        self.thread.join(10)
        self.warden.stop()
        self.sshd.__exit__(*a)
        shutil.rmtree(self.tmp, ignore_errors=True)


def test_the_live_map_and_its_actions_in_a_browser():
    with Rig() as rig, sync_playwright() as pw:
        b = pw.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        try:
            _drive(rig, b)
        finally:
            b.close()


def _drive(rig, b):
    ctx = b.new_context(viewport={"width": 1400, "height": 860})
    ctx.add_init_script("""try { localStorage.setItem('o77w.view', 'split');
      localStorage.setItem('f2o77.city3d', JSON.stringify({quality: 'low', glow: false, buildings: true, real: false})); } catch (_) {}""")
    page = ctx.new_page()
    page.set_default_timeout(60000)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(rig.url)
    page.wait_for_function("() => document.querySelector('.c3') && document.querySelector('.c3').c3go", timeout=120000)

    # ---- sign in inside the panel (the server's own page, framed by the app)
    fr = page.frame_locator("iframe.w-frame")
    fr.locator("#u").fill("owner", timeout=90000)
    fr.locator("#p").fill("correct horse")
    fr.locator("#go").click(force=True, timeout=90000)
    page.wait_for_function("() => document.querySelectorAll('.w-row').length === 5", timeout=60000)
    assert "Warden signed in" in page.inner_text(".w-status")
    page.click(".w-tabs button[data-v=map]", force=True)

    # the panel's anti-forgery token and header name, read in its frame (the panel itself sent none yet)
    page.wait_for_function("""() => { const w = document.querySelector('iframe.w-frame').contentWindow.__o77w;
        return w && w.names.length && Object.keys(w.tokens).length; }""", timeout=30000)
    seen = page.evaluate("""() => { const w = document.querySelector('iframe.w-frame').contentWindow.__o77w;
        return { headers: Object.keys(w.headers), tokens: Object.keys(w.tokens), names: w.names }; }""")
    assert "answer:csrf" in seen["tokens"] and "X-Warden-Csrf" in seen["names"], seen
    assert not any("csrf" in k.lower() for k in seen["headers"]), seen

    def toast_after(fn, pattern, seconds=30):
        page.evaluate("() => document.querySelectorAll('#toasts .toast').forEach(t => { t.dataset.old = '1'; })")
        fn()
        t0 = time.time()
        while time.time() - t0 < seconds:
            for t in page.evaluate("() => [...document.querySelectorAll('#toasts .toast:not([data-old])')].map(t => t.textContent)"):
                if re.search(pattern, t):
                    return t
            page.wait_for_timeout(250)
        shown = page.evaluate("() => document.getElementById('toasts').textContent")
        raise AssertionError(f"no toast {pattern!r}: {shown}")

    def row(name):
        page.click(f".w-row:has-text('{name}')", force=True)
        page.wait_for_selector(f".w-card:not(.hidden) .w-chead b:has-text('{name}')", timeout=15000)

    def button(label):
        page.click(f".w-card .w-btn:has-text('{label}')", force=True)

    def confirm(label):
        page.click(f".modal-back .modal .actions button:has-text('{label}')", force=True)

    def row_class(name, pred, seconds=15):
        t0, cls = time.time(), None
        while time.time() - t0 < seconds:
            cls = page.evaluate("(n) => { const r = [...document.querySelectorAll('.w-row')].find(e => e.textContent.includes(n)); return r ? r.className : ''; }", name)
            if pred(cls):
                return cls
            page.wait_for_timeout(300)
        raise AssertionError(f"{name}: {cls}")

    def wait_hover():
        for _ in range(80):
            page.wait_for_timeout(500)
            hv = page.evaluate("() => document.querySelector('.c3').c3state().hover")
            if hv:
                return hv
        raise AssertionError("nothing under the cursor")

    # ---- the holograms leave trails (drawn from both sides: a flat ribbon on the ground)
    page.wait_for_function("""() => { const t = document.querySelector('.c3').c3trails().find(t => t.key === 'id:3');
        return t && t.samples >= 3 && t.drawn >= 12; }""", timeout=60000)
    tr = {t["key"]: t for t in page.evaluate("() => document.querySelector('.c3').c3trails()")}
    assert all(t["visible"] and t["bothSides"] and t["span"] == 30 for t in tr.values()), tr
    assert tr["id:11"]["samples"] <= 1 and tr["id:13"]["samples"] <= 1, tr           # (the dead and the frozen stay put)

    # ---- revive, thaw, freeze
    row("Takemura")
    assert "null" not in page.inner_text(".w-card")
    assert "revived" in toast_after(lambda: (button("Revive"), confirm("Revive")), "Revive Takemura")
    row_class("Takemura", lambda c: "st-alive" in c)
    row("Panam")
    assert "released" in toast_after(lambda: (button("Thaw"), confirm("Thaw")), "Thaw Panam")
    row_class("Panam", lambda c: "st-frozen" not in c)
    row("Panam")
    assert "frozen" in toast_after(lambda: (button("Freeze"), confirm("Freeze")), "Freeze Panam")
    row_class("Panam", lambda c: "st-frozen" in c)

    # ---- move Jackie to a spot clicked on the map
    page.evaluate("() => document.querySelector('.c3').c3go({x: 50, y: 50, z: 9, az: 0, el: 62, d: 160})")
    row("Jackie")
    button("Move")
    page.click(".w-menu button:has-text('To a spot on the map')", force=True)
    box = page.locator(".w-map canvas").first.bounding_box()
    page.mouse.move(box["x"] + box["width"] * 0.5 - 15, box["y"] + box["height"] * 0.5)
    page.mouse.move(box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.5)
    hv = wait_hover()
    page.mouse.down()
    page.mouse.up()
    page.wait_for_selector(".modal-back .modal h2:has-text('Move Jackie')", timeout=30000)
    m = re.search(r"to (-?[\d.]+), (-?[\d.]+), (-?[\d.]+)\?", page.inner_text(".modal-back .modal"))
    assert m, page.inner_text(".modal-back .modal")
    want = [float(v) for v in m.groups()]
    assert abs(want[0] - hv["p"][0]) < 0.2 and abs(want[1] - hv["p"][1]) < 0.2 and 7 < want[2] < 11, (want, hv)
    assert "arrived" in toast_after(lambda: confirm("Move"), "Move Jackie")
    for _ in range(60):
        page.wait_for_timeout(500)
        j = next((p for p in page.evaluate("() => document.querySelector('.c3').c3players()") if p["name"] == "Jackie"), None)
        if j and abs(j["at"][0] - want[0]) < 1 and abs(j["at"][1] - want[1]) < 1:
            break
    else:
        raise AssertionError(f"Jackie's hologram is at {j}, not {want}")
    assert re.search(r"%.1f, %.1f" % (want[0], want[1]), page.inner_text(".w-card .w-cpos"))

    # ---- a driver: refused without the box, then taken out of the car beside Valerie
    for tick, outcome in ((False, "vehicle"), (True, "arrived")):
        row("Rogue")
        button("Move")
        page.click(".w-menu button:has-text('Beside another player')", force=True)
        page.select_option(".modal-back .modal select", label="Valerie (#3)")
        confirm("Next")
        page.wait_for_selector(".modal-back .modal .w-check input", timeout=15000)
        if tick:
            page.check(".modal-back .modal .w-check input", force=True)
        assert outcome in toast_after(lambda: confirm("Move"), "Move Rogue")
    row_class("Rogue", lambda c: "st-vehicle" not in c)

    # ---- bring Jackie to Valerie, warn Valerie, kick Panam
    row("Valerie")
    button("Move")
    page.click(".w-menu button:has-text('Bring another player to Valerie')", force=True)
    page.select_option(".modal-back .modal select", label="Jackie (#5)")
    confirm("Next")
    page.wait_for_selector(".modal-back .modal h2:has-text('Move Jackie')", timeout=15000)
    assert "arrived" in toast_after(lambda: confirm("Move"), "Move Jackie")
    row("Valerie")
    button("Warn")
    page.fill(".modal-back .modal textarea", "Please stay on the road.")
    assert "sent" in toast_after(lambda: confirm("Send"), "Warn Valerie")
    row("Panam")
    button("Kick")
    page.fill(".modal-back .modal input[type=text]", "AFK in the plaza")
    assert "kicked" in toast_after(lambda: confirm("Kick"), "Kick Panam")
    page.wait_for_function("() => document.querySelectorAll('.w-row').length === 4", timeout=20000)
    assert page.is_hidden(".w-card")

    # what the fake Warden was asked, field by field
    acts = {}
    for path, body in rig.warden.actions:
        acts.setdefault(path.split("/")[-1], []).append((int(path.split("/")[-2]), body))
    assert [pid for pid, _ in acts["heal"]] == [11] and acts["kick"] == [(13, {"reason": "AFK in the plaza"})]
    tp = acts["teleport"]
    assert tp[0][0] == 5 and abs(tp[0][1]["x"] - want[0]) < 0.01 and tp[0][1]["leaveVehicle"] is False
    assert tp[1][0] == 8 and tp[1][1]["targetPlayerId"] == 3 and tp[1][1]["leaveVehicle"] is False
    assert tp[2][0] == 8 and tp[2][1]["leaveVehicle"] is True
    assert tp[3][0] == 5 and tp[3][1]["targetPlayerId"] == 3
    assert acts["warn"] == [(3, {"message": "Please stay on the road.", "reason": "Please stay on the road."})]

    # ---- follow Valerie; the chip stops it; a click on Takemura's hologram opens his card
    row("Valerie")
    button("Follow")
    page.wait_for_function("() => document.querySelector('.c3').c3follow() === 'id:3'", timeout=15000)
    assert page.is_visible(".c3-follow") and page.is_visible(".w-row:has-text('Valerie') .fl")
    assert page.is_visible(".w-card .w-btn:has-text('Stop following')")
    page.click(".c3-follow button", force=True)
    page.wait_for_function("() => document.querySelector('.c3').c3follow() === null", timeout=15000)
    page.click(".w-card .w-icon", force=True)
    tk = next(p for p in page.evaluate("() => document.querySelector('.c3').c3players()") if p["name"] == "Takemura")
    page.evaluate("(p) => document.querySelector('.c3').c3go({x: p.at[0], y: p.at[1], z: p.at[2], az: 0, el: 35, d: 60})", tk)
    page.wait_for_timeout(3000)
    box = page.locator(".w-map canvas").first.bounding_box()
    for dy in (0, -10, -20, -30, -45, -60, 10):
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2 + dy)
        page.wait_for_timeout(300)
        page.mouse.down()
        page.mouse.up()
        page.wait_for_timeout(600)
        if page.is_visible(".w-card:not(.hidden)"):
            break
    assert page.inner_text(".w-card .w-chead b") == "Takemura"
    shots = os.environ.get("O77W_PAGE_SHOTS")
    if shots:
        os.makedirs(shots, exist_ok=True)
        page.screenshot(path=os.path.join(shots, "warden_map.png"), timeout=170000)

    # ---- an anti-forgery refusal is explained; one action in the panel teaches the app its header
    page.evaluate("() => { const w = document.querySelector('iframe.w-frame').contentWindow.__o77w; for (const k of Object.keys(w.tokens)) delete w.tokens[k]; }")
    row("Valerie")
    button("Heal")
    confirm("Heal")
    page.wait_for_selector(".modal-back .modal h2:has-text('refused')", timeout=20000)
    text = page.inner_text(".modal-back .modal")
    assert "cross-site" in text and "one action in the Warden panel" in text, text
    confirm("Show the panel")
    page.wait_for_function("() => document.querySelector('.w-main').classList.contains('view-split')", timeout=10000)
    fr.locator("body").evaluate("() => window.__wardenHeal(5)")
    page.wait_for_function("""() => Object.keys(document.querySelector('iframe.w-frame').contentWindow.__o77w.headers)
        .some(k => k.toLowerCase() === 'x-warden-csrf')""", timeout=15000)
    row("Valerie")
    assert "healed" in toast_after(lambda: (button("Heal"), confirm("Heal")), "Heal Valerie")

    # the app's log: each action with the names of the fields and headers sent, never a token
    log = page.evaluate("() => fetch('/__o77w/api/log?since=0').then(r => r.json())")
    lines = [x["text"] for x in log["lines"] if x["text"].startswith("action ")]
    assert len(lines) == 11, lines
    assert all("X-Warden-Csrf" in x for x in lines if "-> 200" in x), lines
    tokens = set(rig.warden.sessions.values())
    assert not any(t in x for x in lines for t in tokens)
    assert not errors, errors


def test_the_city_is_read_on_its_own_the_first_time():
    """No 3D city yet: a server's page reads it from the game at once (no button to find), then shows it."""
    import test_citymap as TC
    from fivem2open77.studio.worldops import WorldOps
    from fivem2open77.world import citymap as CM
    src = tempfile.mkdtemp(prefix="o77w-city-")
    game = tempfile.mkdtemp(prefix="o77w-game-")
    built = []

    def fake_builder(out_dir, job):
        time.sleep(1.5)                                   # (long enough to see the progress)
        raw, glb, js = TC._fixture(src)
        meta, data = CM.assemble(raw, glb, js, log=job.log, buf_dir=os.path.join(src, "buf"))
        built.append(out_dir)
        return CM._save(out_dir, meta, data, None, log=job.log)

    global _city
    keep = _city
    _city = lambda ws: None                               # noqa: E731 -- (this rig starts without a city)
    WorldOps.citymap_builder = staticmethod(fake_builder)
    try:
        with Rig() as rig, sync_playwright() as pw:
            # a game folder and a WolvenKit to read it with (the builder stands in for both)
            rig.app.toolbox = lambda refresh=False: SimpleNamespace(game_dir=game, wolvenkit="wk", games=[(game, "test")], dotnet=None)
            b = pw.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
            try:
                ctx = b.new_context(viewport={"width": 1200, "height": 800})
                ctx.add_init_script("""try { localStorage.setItem('o77w.view', 'map');
                  localStorage.setItem('f2o77.city3d', JSON.stringify({quality: 'low', glow: false, buildings: true, real: false})); } catch (_) {}""")
                page = ctx.new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(rig.url)
                page.wait_for_selector(".c3-veil-text:has-text('Open77 Warden reads the game')", timeout=60000)
                assert not page.is_visible(".c3-veil button:has-text('Build the 3D city'):not([disabled])")
                page.wait_for_function("() => document.querySelector('.c3') && document.querySelector('.c3').c3go", timeout=120000)
                assert built and built[0] == os.path.join(rig.app.workspace, "citymap")
                assert not errors, errors
            finally:
                b.close()
    finally:
        WorldOps.citymap_builder = None
        _city = keep
        shutil.rmtree(src, ignore_errors=True)
        shutil.rmtree(game, ignore_errors=True)


def test_warden_s_first_run_setup_can_be_scrolled_in_a_small_window():
    """Warden's first-run page (the owner account and the setup PIN) does not scroll itself -- its body is
    overflow: hidden, like all of Warden's pages -- and is a column at least as tall as the window: in a small
    window the end of the form is cut off.  The app lets that page scroll inside its frame: a wheel over it
    brings the confirm-password box and the button into sight, and back up the title; the owner account is made
    from the app's window; then Warden's own panel, which fits the frame, is left exactly as it is."""
    with Rig(setup_pin="38339132") as rig, sync_playwright() as pw:
        b = pw.chromium.launch(args=["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        try:
            ctx = b.new_context(viewport={"width": 1280, "height": 560})
            ctx.add_init_script("""try { localStorage.setItem('o77w.view', 'panel');
              localStorage.setItem('f2o77.city3d', JSON.stringify({quality: 'low', glow: false, buildings: true, real: false})); } catch (_) {}""")
            page = ctx.new_page()
            page.goto(rig.url)
            fr = page.frame_locator("iframe.w-frame")
            fr.locator("#sgo").wait_for(state="attached", timeout=60000)
            page.wait_for_function("""() => { const f = document.querySelector('iframe.w-frame');
                return !!(f && f.contentWindow && f.contentWindow.__o77w && f.contentWindow.__o77w.unclipped); }""", timeout=30000)
            # where things are in the window: the frame's top and bottom edge are the top and bottom of what is seen
            geo = """() => { const f = document.querySelector('iframe.w-frame'), d = f.contentDocument, fr = f.getBoundingClientRect();
                const at = sel => { const r = d.querySelector(sel).getBoundingClientRect(); return { top: fr.top + r.top, bottom: fr.top + r.bottom }; };
                return { seenTop: fr.top, seenBottom: Math.min(innerHeight, fr.bottom), frameHeight: fr.height,
                         panelHeight: document.querySelector('.w-panel').getBoundingClientRect().height,
                         button: at('#sgo'), confirm: at('#sp2'), title: at('.gate h2'),
                         scrolled: Math.max(d.scrollingElement.scrollTop, d.body.scrollTop),
                         bodyOverflow: getComputedStyle(d.body).overflowY }; }"""
            g = page.evaluate(geo)
            assert abs(g["frameHeight"] - g["panelHeight"]) <= 2, g                    # (the frame is the panel's size)
            assert g["button"]["bottom"] > g["seenBottom"] and g["bodyOverflow"] == "auto", g   # cut off, and now it scrolls
            box = page.locator("iframe.w-frame").bounding_box()
            page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            for _ in range(10):
                page.mouse.wheel(0, 300)                               # (the wheel over Warden's page)
                page.wait_for_timeout(120)
            g = page.evaluate(geo)
            for k in ("button", "confirm"):                                              # in sight
                assert g[k]["top"] >= g["seenTop"] and g[k]["bottom"] <= g["seenBottom"], (k, g)
            for _ in range(10):
                page.mouse.wheel(0, -300)
                page.wait_for_timeout(120)
            g = page.evaluate(geo)
            assert g["title"]["top"] >= g["seenTop"] and g["scrolled"] == 0, g          # and the title, back up
            fr.locator("#spin").fill("38339132")
            fr.locator("#su").fill("matthew")
            fr.locator("#sp").fill("a long passphrase")
            fr.locator("#sp2").fill("a long passphrase")
            fr.locator("#sgo").click()
            page.wait_for_function("() => document.querySelectorAll('.w-row').length === 5", timeout=60000)
            assert rig.warden.owner == "matthew"
            # Warden's own panel fits its frame: it does not scroll its body, and the app leaves it so
            page.wait_for_function("""() => { const f = document.querySelector('iframe.w-frame'), d = f.contentDocument;
                return !!(d && d.getElementById('t') && f.contentWindow.__o77w); }""", timeout=30000)
            page.wait_for_timeout(1800)                                   # (past capture.js's own look every 1.5 s)
            st = page.evaluate("""() => { const f = document.querySelector('iframe.w-frame'), d = f.contentDocument;
                return { overflow: getComputedStyle(d.body).overflowY, unclipped: f.contentWindow.__o77w.unclipped,
                         inline: d.body.style.getPropertyValue('overflow-y') }; }""")
            assert st == {"overflow": "hidden", "unclipped": False, "inline": ""}, st
        finally:
            b.close()

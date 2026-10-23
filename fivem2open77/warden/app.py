"""The Warden app's state: its settings, the servers and their panels, the SSH connections, the
background jobs, and Night City (the same reading of the game as the Studio's Live maps: the base
map and the game's own meshes, read from the installed game into this app's own cache).

Mixes in studio.worldops.WorldOps for the city: it needs self.workspace, self.jobs, self.toolbox().
"""
from __future__ import annotations

import os
import shutil
import threading
import time

from ..studio import deploy as deploy_mod
from ..studio.jobs import EventBus, JobManager
from ..studio.secrets import SecretStore
from ..studio.tools import _valid_game, find_dotnet, find_game_dirs, find_wolvenkit
from ..studio.worldops import WorldOps
from . import VERSION
from . import config as C
from .logbook import log, warn
from .proxy import bind_panel
from .tunnel import Discovery, Hosts, TunnelError


class Tools:
    """The game folder and (optional) WolvenKit, as WorldOps asks for them."""

    def __init__(self, game_dir, games, wolvenkit, dotnet):
        self.game_dir = game_dir
        self.games = games
        self.wolvenkit = wolvenkit
        self.dotnet = dotnet

    def wolvenkit_obj(self, log=print):
        from ..cp77.wolvenkit import WolvenKit
        if self.dotnet:
            os.environ["DOTNET_ROOT"] = os.path.dirname(self.dotnet)
            os.environ["DOTNET"] = self.dotnet
        return WolvenKit(self.wolvenkit, dotnet=self.dotnet, log=log)


class WardenApp(WorldOps):
    def __init__(self, workspace: str, token: str):
        self.workspace = os.path.abspath(workspace)
        os.makedirs(self.workspace, exist_ok=True)
        self.token = token
        self.started = time.time()
        self.settings = C.Settings(os.path.join(self.workspace, "settings.json"))
        self.own_secrets = SecretStore(os.path.join(self.workspace, "secrets.json"))
        self.bus = EventBus()
        self.jobs = JobManager(self.bus)
        self.hosts = Hosts()
        self.lock = threading.RLock()
        self.panels = {}                 # target key -> {"target", "server": PanelServer, "discovery"}
        self.home = None
        self._tools = None
        self._tools_at = 0.0
        self.projects = None             # (WorldOps' world edits are the Studio's, not used here)
        kh = os.path.join(self.workspace, "known_hosts")
        studio = self.studio_link()
        if not os.path.isfile(kh) and studio is not None and os.path.isfile(studio.known_hosts):
            try:
                shutil.copyfile(studio.known_hosts, kh)          # the hosts the Studio already trusts
                log("known hosts: copied the Studio's", src="app")
            except OSError as e:
                warn(f"known hosts: the Studio's could not be copied ({e})", src="app")
        deploy_mod.Remote.known_hosts = kh
        log(f"Open77 Warden {VERSION}: workspace {self.workspace}", src="app")

    # ------------------------------------------------------------------ settings, the Studio
    def studio_link(self):
        s = self.settings.get()
        if not s["studio"]["use"]:
            return None
        link = C.StudioLink(C.studio_workspace(s["studio"]["workspace"]))
        return link if link.present() else None

    def toolbox(self, refresh=False):
        if self._tools is not None and not refresh and time.time() - self._tools_at < 30:
            return self._tools
        s = self.settings.get()
        studio = self.studio_link()
        sp = studio.paths() if studio else {"game_dir": "", "wolvenkit": ""}
        game = s["paths"]["game_dir"] or sp["game_dir"]
        games = [(game, "settings")] if _valid_game(game) else find_game_dirs()
        wk = find_wolvenkit(s["paths"]["wolvenkit"] or sp["wolvenkit"])
        self._tools = Tools(games[0][0] if games else None, games, wk, find_dotnet("") if wk and wk.lower().endswith(".dll") else None)
        self._tools_at = time.time()
        return self._tools

    def secrets_for(self, studio):
        ids = {s["id"] for s in studio.servers()} if studio is not None else set()
        return C.Secrets(self.own_secrets, studio, ids)

    # ------------------------------------------------------------------ the panels
    def targets(self) -> list:
        return C.targets(self.settings.get(), self.studio_link())

    def refresh_panels(self):
        """A listener for every target (new ones started, removed ones closed), ports kept."""
        with self.lock:
            ts = self.targets()
            keys = {t["key"] for t in ts}
            for k in list(self.panels):
                if k not in keys:
                    p = self.panels.pop(k)
                    log(f"{k}: no longer in the servers: its panel closes", src="app")
                    threading.Thread(target=p["server"].shutdown, daemon=True).start()
            studio = self.studio_link()
            secrets = self.secrets_for(studio)
            used = {p["server"].port for p in self.panels.values()} | ({self.home.port} if self.home else set())
            changed = False
            for t in ts:
                host = self.hosts.get(t["server"], secrets)
                p = self.panels.get(t["key"])
                if p is not None:
                    if p["target"]["slug"] == t["slug"]:
                        p["target"].update(server=t["server"], folder=t["folder"], name=t["name"], source=t["source"])
                        if p["discovery"].host is not host or p["discovery"].folder != t["folder"]:
                            p["discovery"] = Discovery(host, t["folder"])
                        continue
                    threading.Thread(target=p["server"].shutdown, daemon=True).start()
                want = t["port"] or self._free_port(used)
                srv = bind_panel(self, t, self.token, t["slug"], want)
                used.add(srv.port)
                threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True,
                                 name=f"panel-{t['slug']}").start()
                self.panels[t["key"]] = {"target": t, "server": srv, "discovery": Discovery(host, t["folder"])}
                log(f"{t['key']}: panel at {srv.origin}/ ({t['server']['user']}@{t['server']['host']}:{t['folder']})", src="app")
                if srv.port != t["port"]:
                    changed = True
                    self.settings.data["targets"][t["key"]] = {"port": srv.port}
            if changed:
                self.settings.save()
            return [self.panels[t["key"]] for t in ts]

    def _free_port(self, used) -> int:
        taken = used | {v["port"] for v in self.settings.get()["targets"].values()}
        p = C.PORT_BASE
        while p in taken:
            p += 1
        return p

    def panel(self, key):
        with self.lock:
            p = self.panels.get(key)
        if p is None:
            self.refresh_panels()
            with self.lock:
                p = self.panels.get(key)
        return p

    def panel_endpoint(self, target) -> dict:
        """Where the target's Warden listens on its host ({host, port}); TunnelError when it is off
        or the host cannot be read."""
        p = self.panel(target["key"])
        if p is None:
            raise TunnelError("this server is no longer in the settings")
        info = p["discovery"].get()
        w = info["warden"]
        if not w["enabled"]:
            raise TunnelError(f"Warden is off on this server: set \"warden\": {{\"enabled\": true, \"listenUrl\": "
                              f"\"http://127.0.0.1:11780\"}} in {info['path']} and restart the server")
        if w.get("scheme") == "https":
            raise TunnelError("this server's Warden listens with https: the app reaches http listeners "
                              "(Warden on the host's loopback over SSH needs no TLS)")
        return w

    def panel_channel(self, target):
        p = self.panel(target["key"])
        w = self.panel_endpoint(target)
        try:
            return p["discovery"].host.channel(w["host"], w["port"])
        except TunnelError:
            p["discovery"].forget()                     # (Warden may have moved: read server.jsonc again)
            raise

    def check(self, key) -> dict:
        p = self.panel(key)
        if p is None:
            raise KeyError(key)
        p["discovery"].forget()
        return self.target_state(p, probe=True)

    def target_state(self, p, probe=False) -> dict:
        t, srv, d = p["target"], p["server"], p["discovery"]
        info, error = d.info, None
        if probe:                                       # (first: it connects, the state then says so)
            try:
                info = d.get(refresh=True)
            except TunnelError as e:
                error, info = str(e), None
        out = {"key": t["key"], "name": t["name"], "label": t["server"].get("label") or t["server"]["id"],
               "server": t["server"]["id"], "folder": t["folder"], "source": t["source"], "slug": t["slug"],
               "url": srv.origin + "/__o77w/", "origin": srv.origin, "port": srv.port,
               "ssh": d.host.state(), "requests": srv.requests, "errors": srv.errors,
               "last_status": srv.last_status, "last_at": srv.last_at or None}
        if error:
            out["error"] = error
        if info:
            out.update(display=info["name"] or t["name"], warden=info["warden"], rcon=info["rcon"])
        elif d.error:
            out["error"] = d.error
        return out

    def state(self, here=None) -> dict:
        panels = self.refresh_panels()
        tb = self.toolbox()
        s = self.settings.get()
        studio = self.studio_link()
        return {"app": "open77-warden", "version": VERSION, "workspace": self.workspace,
                "here": here, "targets": [self.target_state(p) for p in panels],
                "home": self.home.origin + "/__o77w/" if self.home else None,
                "studio": {"present": studio is not None, "workspace": studio.workspace if studio else None,
                           "use": s["studio"]["use"]},
                "game": {"dir": tb.game_dir, "found": [{"path": g, "where": w} for g, w in tb.games]},
                "wolvenkit": tb.wolvenkit, "poll_seconds": s["poll_seconds"], "trail_seconds": s["trail_seconds"],
                "last": s["last"]}

    def close(self):
        with self.lock:
            for p in self.panels.values():
                try:
                    p["server"].shutdown()
                    p["server"].server_close()
                except Exception:   # noqa: BLE001
                    pass
            self.panels.clear()
        self.hosts.close()

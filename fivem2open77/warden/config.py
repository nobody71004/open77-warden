"""Open77 Warden's settings, and the servers it shows.

The servers are the Studio's (fivem2open77 Studio, Settings > Servers: an SSH host and the Open77
server folders on it), read where the Studio keeps them each time, so a server added or changed
there shows here too; their passwords come from the Studio's own encrypted store (the same Windows
user).  Servers can also be added here (the same form), for a PC without the Studio.

Every Open77 server folder is a *target*: one Warden panel, at a local address of its own
(http://<slug>.localhost:<port>/: the name keeps each panel's sign-in apart, the port stays the
same between runs so the panel's own settings stay too).

    settings.json   {"paths": {...}, "studio": {...}, "servers": [...], "targets": {key: {"port": n}}, ...}
    secrets.json    the passwords of servers added here (DPAPI on Windows, see studio/secrets.py)
"""
from __future__ import annotations

import copy
import json
import os
import re
import threading

from ..studio.secrets import SecretStore
from ..studio.settings import SettingsError, validate_server

SCHEMA = 1
DEFAULTS = {
    "schema": SCHEMA,
    "paths": {"game_dir": "", "wolvenkit": ""},
    "studio": {"use": True, "workspace": ""},          # the Studio's servers (and game folder) when it is installed
    "servers": [],                                      # servers added here (the Studio's form)
    "targets": {},                                      # key -> {"port": n}
    "home_port": 0,
    "last": "",                                         # the panel shown last (a target key)
    "poll_seconds": 2.0,                                # how often the live map asks Warden for the roster
    "trail_seconds": 30,
}
PORT_BASE = 47781                                       # the first panel's port (then the next free one)
HOME_PORT = 47780
SLUG_RX = re.compile(r"[^a-z0-9]+")


def default_workspace() -> str:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(base, "open77-warden")
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "open77-warden")


def studio_workspace(configured: str = "") -> str:
    if configured:
        return configured
    env = os.environ.get("F2O77_WORKSPACE")
    if env:
        return env
    from ..studio.paths import default_workspace as studio_default
    return studio_default()


def _merge(base, patch):
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def validate(data: dict) -> dict:
    errors = []
    d = _merge(DEFAULTS, data or {})
    for k in list(d):
        if k not in DEFAULTS:
            d.pop(k)                                    # (a newer app's setting: kept out, not fatal)
    for k in ("game_dir", "wolvenkit"):
        if not isinstance(d["paths"].get(k), str):
            errors.append(f"paths.{k}: text")
    d["studio"]["use"] = bool(d["studio"].get("use", True))
    if not isinstance(d["studio"].get("workspace"), str):
        errors.append("studio.workspace: text")
    servers, ids = [], set()
    for s in d["servers"] if isinstance(d["servers"], list) else []:
        try:
            v = validate_server(s)
        except SettingsError as e:
            errors.extend(e.errors)
            continue
        if v["id"] in ids:
            errors.append(f"server id {v['id']} twice")
            continue
        ids.add(v["id"])
        servers.append(v)
    d["servers"] = servers
    t = {}
    for k, v in (d["targets"] or {}).items() if isinstance(d["targets"], dict) else []:
        try:
            p = int((v or {}).get("port") or 0)
        except (TypeError, ValueError):
            p = 0
        if 1024 <= p <= 65535:
            t[str(k)] = {"port": p}
    d["targets"] = t
    try:
        d["poll_seconds"] = max(1.0, min(30.0, float(d["poll_seconds"])))
    except (TypeError, ValueError):
        errors.append("poll_seconds: a number of seconds")
    try:
        d["trail_seconds"] = max(0, min(600, int(d["trail_seconds"])))
    except (TypeError, ValueError):
        errors.append("trail_seconds: a number of seconds")
    try:
        d["home_port"] = int(d["home_port"] or 0)
    except (TypeError, ValueError):
        d["home_port"] = 0
    d["last"] = str(d.get("last") or "")[:200]
    if errors:
        raise SettingsError(errors)
    return d


class Settings:
    def __init__(self, path: str):
        self.path = path
        self.lock = threading.RLock()
        self.data = self._read()

    def _read(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            raw = {}
        try:
            return validate(raw)
        except SettingsError:
            # a broken file: keep what is valid of it
            raw = dict(raw or {})
            raw["servers"] = [s for s in raw.get("servers") or [] if _valid_server(s)]
            try:
                return validate(raw)
            except SettingsError:
                return validate({})

    def save(self):
        with self.lock:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=1)
            os.replace(tmp, self.path)

    def get(self) -> dict:
        with self.lock:
            return copy.deepcopy(self.data)

    def update(self, patch: dict) -> dict:
        with self.lock:
            self.data = validate(_merge(self.data, patch))
            self.save()
            return self.get()


def _valid_server(s) -> bool:
    try:
        validate_server(s)
        return True
    except (SettingsError, TypeError, AttributeError):
        return False


# ---------------------------------------------------------------------- the Studio's servers
class StudioLink:
    """The Studio's settings and secrets, read live (it may change them while this runs)."""

    def __init__(self, workspace: str):
        self.workspace = workspace
        self.settings_path = os.path.join(workspace, "settings.json")
        self.secrets = SecretStore(os.path.join(workspace, "secrets.json"))
        self.known_hosts = os.path.join(workspace, "known_hosts")

    def present(self) -> bool:
        return os.path.isfile(self.settings_path)

    def _settings(self) -> dict:
        try:
            with open(self.settings_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def servers(self) -> list:
        out = []
        for s in self._settings().get("servers") or []:
            try:
                out.append(validate_server(s))
            except (SettingsError, TypeError, AttributeError):
                continue
        return out

    def paths(self) -> dict:
        p = self._settings().get("paths") or {}
        return {"game_dir": str(p.get("game_dir") or ""), "wolvenkit": str(p.get("wolvenkit") or "")}


class Secrets:
    """A server's password: the Studio's store for the Studio's servers, ours for ours."""

    def __init__(self, own: SecretStore, studio: StudioLink | None, studio_ids):
        self.own = own
        self.studio = studio
        self.studio_ids = set(studio_ids or ())

    def get(self, key: str):
        m = re.match(r"^server:([^:]+):", key)
        if m and self.studio is not None and m.group(1) in self.studio_ids:
            return self.studio.secrets.get(key)
        return self.own.get(key)


# ---------------------------------------------------------------------- targets
def slug_of(server_id: str, name: str, taken: set) -> str:
    """A host name label for a panel: the folder's name (and the server's id when two clash)."""
    base = SLUG_RX.sub("-", name.lower()).strip("-")[:40] or "server"
    s = base
    if s in taken:
        s = (base + "-" + SLUG_RX.sub("-", server_id.lower()).strip("-"))[:63].strip("-")
    n = 2
    while s in taken or s == "warden":
        s = f"{base[:56]}-{n}"
        n += 1
    taken.add(s)
    return s


def targets(settings: dict, studio: StudioLink | None) -> list:
    """Every Open77 server folder: [{key, server, name, folder, slug, port, source}].  The Studio's
    servers first (its order), then ours; an id the Studio also has is the Studio's."""
    out, taken, seen = [], set(), set()
    groups = []
    if studio is not None and settings["studio"]["use"]:
        groups.append(("studio", studio.servers()))
    groups.append(("warden", settings["servers"]))
    for source, servers in groups:
        for srv in servers:
            if srv["id"] in seen:
                continue
            seen.add(srv["id"])
            for name in srv["names"]:
                key = f"{srv['id']}/{name}"
                folder = srv["servers_root"].rstrip("/") + "/" + name
                out.append({"key": key, "server": srv, "name": name, "folder": folder,
                            "slug": slug_of(srv["id"], name, taken), "source": source,
                            "port": int((settings["targets"].get(key) or {}).get("port") or 0)})
    return out

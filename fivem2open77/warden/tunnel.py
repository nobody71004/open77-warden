"""The SSH connections to the server hosts, and where each server's Warden listens.

One connection per server host (the Studio's Remote: its known hosts, password or key), shared by
all the panels on that host and kept alive; a panel's request opens a direct-tcpip channel through
it to Warden's listenUrl on the host, so Warden can stay on the host's loopback and nothing new is
opened to the internet.  A connection that drops is made again on the next request (not more often
than every few seconds, so a host that is down is not hammered).

Where Warden listens is in the server's own server.jsonc (warden.enabled, warden.listenUrl), read
over SFTP: nothing runs on the host.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse

from ..studio import deploy as deploy_mod
from ..studio.deploy import DeployError
from .logbook import log, warn

RETRY_AFTER = 8.0                   # seconds between attempts at a host that refused or did not answer
DISCOVER_TTL = 60.0                 # server.jsonc read again after this long
KEEPALIVE = 25                      # SSH keepalive (seconds): NAT and firewalls keep the connection


class TunnelError(Exception):
    """Why a panel cannot be reached (the message says what to do)."""


def strip_jsonc(t: str) -> str:
    """server.jsonc -> JSON: comments and trailing commas out (strings left alone)."""
    out, i, n, s = [], 0, len(t), False
    while i < n:
        c = t[i]
        if s:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(t[i + 1])
                i += 1
            elif c == '"':
                s = False
        elif c == '"':
            s = True
            out.append(c)
        elif t.startswith("//", i):
            while i < n and t[i] != "\n":
                i += 1
            continue
        elif t.startswith("/*", i):
            j = t.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        else:
            out.append(c)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def warden_endpoint(listen_url: str) -> tuple:
    """listenUrl -> (host to connect to on the server host, port).  0.0.0.0 / [::] / * listen on every
    address of the host: its loopback is one of them."""
    u = urllib.parse.urlsplit(listen_url or "")
    if u.scheme not in ("http", "https"):
        raise TunnelError(f"warden.listenUrl '{listen_url}' is not an http:// URL")
    host = (u.hostname or "127.0.0.1").strip("[]")
    if host in ("0.0.0.0", "::", "*", "+", ""):
        host = "127.0.0.1"
    port = u.port or (443 if u.scheme == "https" else 80)
    return host, port, u.scheme


def read_config(text: str) -> dict:
    """What the app needs of a server.jsonc."""
    c = json.loads(strip_jsonc(text))
    w = c.get("warden") or {}
    ident = c.get("identity") or {}
    rcon = c.get("rcon") or {}
    return {"name": str(ident.get("name") or "")[:96], "warden": {"enabled": bool(w.get("enabled")),
            "listenUrl": w.get("listenUrl") or "http://127.0.0.1:11780"},
            "rcon": {"enabled": bool(rcon.get("enabled")), "port": rcon.get("port") or 11782}}


class Host:
    """One SSH connection to a server host."""

    def __init__(self, server: dict, secrets):
        self.server = server
        self.secrets = secrets
        self.lock = threading.RLock()
        self.remote = None
        self.error = None
        self.failed_at = 0.0
        self.connected_at = 0.0
        self.channels = 0

    @property
    def label(self) -> str:
        s = self.server
        return f"{s['user']}@{s['host']}:{s.get('port', 22)}"

    def state(self) -> dict:
        with self.lock:
            up = self.remote is not None and self.remote.alive()
            return {"connected": up, "error": None if up else self.error, "since": self.connected_at if up else None,
                    "host": self.server["host"], "user": self.server["user"], "port": int(self.server.get("port", 22))}

    def _connect(self):
        with self.lock:
            if self.remote is not None and self.remote.alive():
                return self.remote
            if self.remote is not None:
                warn(f"{self.label}: the SSH connection dropped; connecting again", src="ssh")
                try:
                    self.remote.close()
                except Exception:   # noqa: BLE001 -- already gone
                    pass
                self.remote = None
            wait = self.failed_at + RETRY_AFTER - time.time()
            if wait > 0 and self.error:
                raise TunnelError(f"{self.error} (trying again in {wait:.0f} s)")
            t0 = time.time()
            log(f"{self.label}: connecting ({self.server.get('auth')})", src="ssh")
            try:
                r = deploy_mod.Remote(self.server, self.secrets, log=lambda *a: None, timeout=15)
            except DeployError as e:
                self.error, self.failed_at = str(e), time.time()
                warn(f"{self.label}: {e}", src="ssh")
                raise TunnelError(str(e)) from e
            t = r.client.get_transport()
            if t is not None:
                t.set_keepalive(KEEPALIVE)
            self.remote, self.error, self.connected_at = r, None, time.time()
            log(f"{self.label}: connected in {time.time() - t0:.1f} s", src="ssh")
            return r

    def channel(self, host: str, port: int, timeout=15.0):
        """A direct-tcpip channel to host:port as seen from the server host."""
        for attempt in (1, 2):
            r = self._connect()
            t = r.client.get_transport()
            try:
                ch = t.open_channel("direct-tcpip", (host, int(port)), ("127.0.0.1", 0), timeout=timeout)
                with self.lock:
                    self.channels += 1
                return ch
            except Exception as e:  # noqa: BLE001 -- paramiko raises ChannelException, SSHException, EOFError...
                msg = str(e) or e.__class__.__name__
                if "Connect failed" in msg or "Connection refused" in msg or getattr(e, "code", None) == 2:
                    raise TunnelError(f"nothing answers on {host}:{port} on {self.server['host']} "
                                      "(is the Open77 server running, with Warden on?)") from e
                if attempt == 1 and not r.alive():
                    continue                            # the connection died under us: once more on a new one
                raise TunnelError(f"{self.label}: could not open a channel to {host}:{port}: {msg}") from e
        raise TunnelError(f"{self.label}: no connection")

    def read_file(self, path: str, limit=1 << 20) -> str:
        r = self._connect()
        try:
            with self.lock:                             # (one SFTP session per connection: one at a time)
                f = r.sftp.open(path, "r")
                try:
                    data = f.read(limit)
                finally:
                    f.close()
        except FileNotFoundError as e:
            raise TunnelError(f"{path} was not found on {self.server['host']}") from e
        except OSError as e:
            raise TunnelError(f"{path} on {self.server['host']}: {e}") from e
        return data.decode("utf-8", errors="replace")

    def close(self):
        with self.lock:
            if self.remote is not None:
                try:
                    self.remote.close()
                except Exception:   # noqa: BLE001
                    pass
                self.remote = None


class Hosts:
    """The connections, one per server id; a server changed in the settings gets a new one."""

    def __init__(self):
        self.lock = threading.Lock()
        self.by_id = {}

    def get(self, server: dict, secrets) -> Host:
        sig = json.dumps([server.get(k) for k in ("host", "port", "user", "auth", "key_file")])
        with self.lock:
            h = self.by_id.get(server["id"])
            if h is not None and h.sig == sig:
                h.secrets = secrets
                return h
            if h is not None:
                h.close()
            h = Host(server, secrets)
            h.sig = sig
            self.by_id[server["id"]] = h
            return h

    def close(self):
        with self.lock:
            for h in self.by_id.values():
                h.close()
            self.by_id.clear()


class Discovery:
    """Where a server's Warden listens (and its name), from its server.jsonc, kept a minute."""

    def __init__(self, host: Host, folder: str):
        self.host = host
        self.folder = folder
        self.lock = threading.Lock()
        self.info = None
        self.at = 0.0
        self.error = None

    def get(self, refresh=False) -> dict:
        with self.lock:
            if self.info is not None and not refresh and time.time() - self.at < DISCOVER_TTL:
                return self.info
            path = self.folder.rstrip("/") + "/server.jsonc"
            try:
                info = read_config(self.host.read_file(path))
            except (ValueError, KeyError) as e:
                self.error = f"{path}: not readable as JSON ({e})"
                raise TunnelError(self.error) from e
            except TunnelError as e:
                self.error = str(e)
                raise
            info["path"] = path
            if info["warden"]["enabled"]:
                h, p, scheme = warden_endpoint(info["warden"]["listenUrl"])
                info["warden"].update(host=h, port=p, scheme=scheme)
            changed = self.info is None or self.info.get("warden") != info.get("warden")
            self.info, self.at, self.error = info, time.time(), None
            if changed:
                w = info["warden"]
                log(f"{self.host.server['host']}:{path}: '{info['name'] or '?'}', Warden "
                    + (f"on {w['listenUrl']} (reached at {w['host']}:{w['port']} on the host)" if w["enabled"] else "off"),
                    src="discover")
            return info

    def forget(self):
        with self.lock:
            self.info = None

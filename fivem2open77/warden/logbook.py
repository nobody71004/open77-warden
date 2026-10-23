"""The Warden app's log: every line to stdout (the window keeps it in logs\\warden-<time>.log) and the
last lines in memory for the window's Log panel.  Nothing secret is ever written here: no
passwords, tokens or cookies (callers log names and states, never values)."""
from __future__ import annotations

import collections
import sys
import threading
import time

_lock = threading.Lock()
_ring = collections.deque(maxlen=2000)
_seq = 0


def log(*parts, level="info", src="app"):
    """One line: [time] level src: text."""
    global _seq
    text = " ".join(str(p) for p in parts)
    now = time.time()
    with _lock:
        _seq += 1
        entry = {"seq": _seq, "t": now, "level": level, "src": src, "text": text}
        _ring.append(entry)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)) + f".{int(now * 1000) % 1000:03d}"
    try:
        print(f"[{stamp}] {level:5s} {src}: {text}", flush=True)
    except (OSError, ValueError):
        pass
    return entry


def warn(*parts, src="app"):
    return log(*parts, level="warn", src=src)


def error(*parts, src="app"):
    return log(*parts, level="error", src=src)


def since(seq: int = 0, limit: int = 500) -> dict:
    with _lock:
        items = [e for e in _ring if e["seq"] > seq]
        last = _seq
    return {"lines": items[-limit:], "seq": last}


def logger(src: str):
    """log(...) bound to a source name, for code that takes a ``log`` callable."""
    return lambda *parts: log(*parts, src=src)

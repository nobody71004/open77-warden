"""A CR2W file's exports in the shape of WolvenKit's JSON, for code written against WolvenKit's
output (the 3D map's entity and collision meshes, world/citymap.py) that now reads the game
directly (cr2w.py).

    doc(c) -> {"Data": {"RootChunk": <export 0>}}

An export is {"$type": its class, property: value, ...}; a handle is {"HandleId": "n", "Data":
<export n>} the first time it is met and {"HandleRefId": "n"} after; a resource reference is
{"DepotPath": {"$value": path}}; a CName or an enum is its name, a bitfield the names of its flags
joined by ", " (as WolvenKit writes them), a DataBuffer its reference (an int).  A property left at
its default is not in the file, so not here either (WolvenKit writes every one): readers take the
defaults themselves.
"""
from __future__ import annotations

import struct

from .cr2w import BUFFER_TYPES, PRIMS, STATIC_RX, CR2W, CR2WError


def doc(c: CR2W) -> dict:
    memo = set()
    return {"Data": {"RootChunk": export(c, 0, memo) if c.exports else {}}}


def export(c: CR2W, i: int, memo: set) -> dict:
    props, _tail = c.chunk(i)
    out = {"$type": c.exports[i]["cls"]}
    for n, t, o, sz in props:
        try:
            out[n] = value(c, t, o, sz, memo)
        except (CR2WError, IndexError, struct.error):
            out[n] = None
    return out


def handle(c: CR2W, idx: int, memo: set):
    if idx < 0 or idx >= len(c.exports):
        return None
    if idx in memo:
        return {"HandleRefId": str(idx)}
    memo.add(idx)
    return {"HandleId": str(idx), "Data": export(c, idx, memo)}


def _ref(c: CR2W, o: int) -> dict:
    i = struct.unpack_from("<H", c.b, o)[0]
    return {"DepotPath": {"$value": c.imports[i - 1][0] if 0 < i <= len(c.imports) else ""}}


def _struct(c: CR2W, o: int, end: int, memo: set):
    props, p = c.tagged(o, end)
    return {pn: value(c, pt, po, ps, memo) for pn, pt, po, ps in props}, p


def value(c: CR2W, ty: str, o: int, size: int, memo: set):
    b = c.b
    if ty.startswith(("handle:", "whandle:")):
        return handle(c, struct.unpack_from("<i", b, o)[0] - 1, memo)
    if ty.startswith(("raRef:", "rRef:")):
        return _ref(c, o)
    if ty in PRIMS or ty == "CName" or ty in BUFFER_TYPES:
        return c.value(ty, o, size)
    static = STATIC_RX.match(ty)
    if ty.startswith("array:") or static:
        et = ty[6:] if not static else static.group(2)
        n = struct.unpack_from("<I", b, o)[0]
        p, end, out = o + 4, o + size, []
        for _ in range(n):
            if et in PRIMS:
                fmt, w = PRIMS[et]
                out.append(struct.unpack_from(fmt, b, p)[0])
                p += w
            elif et == "CName":
                out.append(c.value(et, p, 2))
                p += 2
            elif et.startswith(("raRef:", "rRef:")):
                out.append(_ref(c, p))
                p += 2
            elif et.startswith(("handle:", "whandle:")):
                out.append(handle(c, struct.unpack_from("<i", b, p)[0] - 1, memo))
                p += 4
            elif b[p] == 0:                                     # a struct, tagged
                v, p = _struct(c, p, end, memo)
                out.append(v)
            else:                                               # an enum: its value's name
                i = struct.unpack_from("<H", b, p)[0]
                out.append(c.names[i] if i < len(c.names) else i)
                p += 2
        return out
    if size > 0 and b[o] == 0:                                  # a struct (or an empty bitfield)
        try:
            return _struct(c, o, o + size, memo)[0]
        except (CR2WError, IndexError, struct.error):
            pass
        if size == 2:
            return ""
    if size == 2:                                               # an enum: its value's name
        i = struct.unpack_from("<H", b, o)[0]
        return c.names[i] if i < len(c.names) else i
    if size >= 4 and size % 2 == 0:                             # a bitfield: its flags' names, then a 0
        idx = struct.unpack_from(f"<{size // 2}H", b, o)
        if idx[-1] == 0 and all(0 < k < len(c.names) for k in idx[:-1]):
            return ", ".join(c.names[k] for k in idx[:-1])
    return None

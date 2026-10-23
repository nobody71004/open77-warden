"""Build Open77 Warden for Windows: a folder with everything it needs, and a one-file setup.exe.

    python tools/package/build_warden.py --from-app DIR --host DIR --sfx-sdk lzma*.7z --out DIR

--from-app: a built fivem2open77 Studio app folder (tools/package/build_windows.py): Open77 Warden ships
            the same runtimes and tools -- runtime/python (Python 3.12 + numpy, paramiko and their
            dependencies), runtime/dotnet and tools/wolvenkit (WolvenKit reads the game's 3D city map when
            the app's own reader cannot) -- and their licences.
--host:     the built open77-warden.exe folder (dotnet build host-warden/Open77Warden.csproj -c Release).
--sfx-sdk:  the LZMA SDK archive (lzma*.7z): its 7zSD.sfx is the setup's stub (it gets the Warden icon).

Layout (F2O77_HOME of the app):
    open77-warden.exe (+ WebView2 dlls)   the window
    app/fivem2open77/                     the Warden app (fivem2open77/warden) and what it shares with the Studio
    runtime/python/  runtime/dotnet/      bundled runtimes
    tools/wolvenkit/                      WolvenKit CLI
    LICENSES/                             third-party licences
Needs 7z on PATH and pefile (pip) to put the icon on the setup.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from fivem2open77.warden import VERSION  # noqa: E402

EXE = "open77-warden.exe"

SFX_CONFIG = """;!@Install@!UTF-8!
Title="Open77 Warden {version}"
RunProgram="open77-warden.exe --install"
GUIMode="1"
;!@InstallEnd@!
"""

README = """Open77 Warden {version}
==================

Your Open77 servers' Warden panels in one window, each beside a live 3D map of Night City with the
game's own buildings and props, and the server's players on it as holograms (updated every few
seconds): their health, ping and state, a trail of where they have been, follow one with the camera,
and Warden's own actions from the map (heal or revive, freeze, move -- to a spot you click, beside
another player, out of a car -- warn, kick), each confirmed first and written in Warden's audit.

Start:   open77-warden.exe             (asks once to install itself to %LOCALAPPDATA%\\Programs)
         open77-warden.exe --browser   use your web browser instead of the app window
Remove:  Settings > Apps > Open77 Warden, or open77-warden.exe --uninstall
Data:    %LOCALAPPDATA%\\open77-warden   (the servers added here, the 3D city read from your game, logs)

Servers: the ones you set up in fivem2open77 Studio (Settings > Servers) are used as they are, with their
SSH keys or passwords; more can be added in the app.  The app reaches each server's Warden through SSH,
as `ssh -L` would: Warden has to be on in the server's server.jsonc ("warden": {{"enabled": true,
"listenUrl": "http://127.0.0.1:11780"}}), nothing else changes on the server.  You sign in to Warden
itself, in the panel beside the map: the map shows what your Warden role may see and do.

The 3D city: read once from your Cyberpunk 2077 install (about a minute), then kept in the app's data.
The game's files are never shipped with the app.

Bundled third-party software (licences in LICENSES\\): Python, numpy, paramiko, cryptography, PyNaCl,
bcrypt, cffi, pycparser, invoke, .NET runtime, WolvenKit CLI, three.js, Microsoft Edge WebView2 SDK.
"""


def sh(*cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if r.returncode != 0:
        raise SystemExit(f"failed: {' '.join(cmd)}\n{r.stdout[-2000:]}")
    return r.stdout


def need(path, what):
    if not os.path.exists(path):
        raise SystemExit(f"missing {what}: {path}")
    return path


def build(a):
    src = os.path.abspath(a.from_app)
    host = os.path.abspath(a.host)
    need(os.path.join(host, EXE), "the window (build host-warden first)")
    out = os.path.abspath(a.out)
    app = os.path.join(out, "open77-warden")
    shutil.rmtree(app, ignore_errors=True)
    os.makedirs(app)

    # the window
    for f in os.listdir(host):
        s = os.path.join(host, f)
        if f.endswith((".pdb", ".xml")) or f == "Microsoft.Web.WebView2.Wpf.dll":
            continue
        (shutil.copytree if os.path.isdir(s) else shutil.copy2)(s, os.path.join(app, f))

    # the runtimes and WolvenKit, as the Studio ships them
    for rel in (("runtime", "python"), ("runtime", "dotnet"), ("tools", "wolvenkit")):
        shutil.copytree(need(os.path.join(src, *rel), "/".join(rel)), os.path.join(app, *rel))
    py = os.path.join(app, "runtime", "python")
    need(os.path.join(py, "python.exe"), "python.exe")
    pth = [p for p in os.listdir(py) if p.endswith("._pth")]
    if len(pth) != 1 or "..\\..\\app" not in open(os.path.join(py, pth[0]), encoding="ascii").read():
        raise SystemExit("runtime/python's ._pth does not put ..\\..\\app on the path")
    for pkg in ("numpy", "paramiko", "cryptography", "nacl", "bcrypt", "cffi"):
        need(os.path.join(py, "Lib", "site-packages", pkg), f"the {pkg} package")
    need(os.path.join(app, "runtime", "dotnet", "dotnet.exe"), "dotnet.exe")
    need(os.path.join(app, "tools", "wolvenkit", "WolvenKit.CLI.dll"), "WolvenKit.CLI.dll")

    # the app (the warden package and the Studio's parts it uses: the 3D city, SSH, the game reader)
    shutil.copytree(os.path.join(ROOT, "fivem2open77"), os.path.join(app, "app", "fivem2open77"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"))
    for rel in (("warden", "__main__.py"), ("warden", "web", "index.html"), ("warden", "web", "capture.js"),
                ("warden", "web", "img", "icon-32.png"), ("studio", "web", "js", "pages", "city3d.js"),
                ("studio", "web", "js", "pages", "cityplayers.js"), ("studio", "web", "vendor", "three", "three.module.min.js")):
        need(os.path.join(app, "app", "fivem2open77", *rel), "/".join(rel))

    # licences
    lic = os.path.join(app, "LICENSES")
    shutil.copytree(os.path.join(src, "LICENSES"), lic)
    for f in os.listdir(lic):
        if f.startswith("7-Zip"):                      # (not shipped with the Warden)
            os.remove(os.path.join(lic, f))
    shutil.copy2(os.path.join(ROOT, "fivem2open77", "studio", "web", "vendor", "three", "LICENSE"), os.path.join(lic, "three.js-LICENSE.txt"))
    with open(os.path.join(app, "README.txt"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write(README.format(version=VERSION))
    info = {}
    try:
        info = json.load(open(os.path.join(src, "app", "build-info.json")))
    except (OSError, ValueError):
        pass
    manifest = {"product": "open77-warden", "version": VERSION, "runtimes_from": f"fivem2open77 Studio {info.get('version', '?')}",
                "wheels": info.get("wheels"), "python": info.get("python"), "dotnet": info.get("dotnet")}
    with open(os.path.join(app, "app", "build-info.json"), "w") as f:
        json.dump(manifest, f, indent=1)

    # one-file setup: 7zSD.sfx (with the Warden icon) + config + 7z archive of the folder
    sfx_dir = os.path.join(out, "_sfx")
    shutil.rmtree(sfx_dir, ignore_errors=True)
    os.makedirs(sfx_dir)
    sh("7z", "e", "-y", f"-o{sfx_dir}", os.path.abspath(a.sfx_sdk), "bin/7zSD.sfx")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from peicon import set_icon
    set_icon(os.path.join(sfx_dir, "7zSD.sfx"), os.path.join(ROOT, "host-warden", "app.ico"))
    archive = os.path.join(out, "_app.7z")
    if os.path.exists(archive):
        os.remove(archive)
    sh("7z", "a", "-t7z", "-mx=7", "-m0=lzma2", "-mmt=on", "-ms=on", archive, ".", cwd=app)
    setup = os.path.join(out, f"open77-warden-{VERSION}-setup.exe")
    with open(setup, "wb") as f:
        with open(os.path.join(sfx_dir, "7zSD.sfx"), "rb") as s:
            shutil.copyfileobj(s, f)
        f.write(SFX_CONFIG.format(version=VERSION).encode("utf-8"))
        with open(archive, "rb") as z:
            shutil.copyfileobj(z, f)
    shutil.rmtree(sfx_dir, ignore_errors=True)
    os.remove(archive)
    size = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(app) for f in fs)
    h = hashlib.sha256(open(setup, "rb").read()).hexdigest()
    print(f"app folder: {app} ({size / 1048576:.0f} MiB)")
    print(f"setup:      {setup} ({os.path.getsize(setup) / 1048576:.1f} MiB) sha256 {h}")
    return setup


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--from-app", required=True)
    p.add_argument("--host", required=True)
    p.add_argument("--sfx-sdk", required=True)
    p.add_argument("--out", required=True)
    build(p.parse_args(argv))
    return 0


if __name__ == "__main__":
    sys.exit(main())

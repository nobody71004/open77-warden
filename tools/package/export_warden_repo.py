"""Export Open77 Warden as a repository of its own (github.com/nobody71004/open77-warden).

    python tools/package/export_warden_repo.py OUT [--source DIR]

What goes in, from this checkout (or --source, another checkout of it):
  * the Warden app: fivem2open77/warden (the local server, the SSH tunnels, the window's pages) and
    host-warden/ (the Windows window and its installer), with the three C# files it links from the
    Studio's host/;
  * what it shares with the fivem2open77 Studio, as the app ships it: every fivem2open77 module the
    app and its tests import (at any depth, lazily or not), the package data those modules read
    (fivem2open77/data), and the Studio's web files the app's page loads (its JavaScript modules,
    stylesheets, fonts and three.js, followed from fivem2open77/warden/web/index.html);
  * its tests and their fixtures, its packaging (tools/package/build_warden.py, peicon.py), its brand
    tool, and a README, CHANGELOG (the Warden's entries), LICENSE (GPLv3, as this repository), and
    tests/test_repo.py, which checks that every import and every file the page loads is in the repo
    (with this script's own closure functions, which the export carries as tools/package/).
OUT must not exist or be empty.  Nothing is committed: OUT is a plain folder.  Exits 1 (and says what)
when something the app needs cannot be found.
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import shutil
import sys

PKG = "fivem2open77"
REPO_URL = "https://github.com/nobody71004/open77-warden"

TESTS = ("test_warden.py", "test_warden_ui.py", "test_remote_run.py", "test_citymap.py")
TOOLS = ("tools/package/build_warden.py", "tools/package/peicon.py",
         "tools/brand/make_warden_brand.py", "tools/brand/make_brand.py")
# Studio web files the page reads that no import names: the licences of what is shipped
WEB_EXTRA = ("fonts/OFL-IBM-Plex.txt", "vendor/three/LICENSE", "vendor/three/README.txt")
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", "bin", "obj")


class ExportError(Exception):
    pass


# ------------------------------------------------------------------------------------- Python
def _module_file(src, name):
    p = os.path.join(src, *name.split("."))
    if os.path.isfile(os.path.join(p, "__init__.py")):
        return os.path.join(p, "__init__.py")
    if os.path.isfile(p + ".py"):
        return p + ".py"
    return None


def imports_of(path, name=None):
    """The fivem2open77 modules a source file imports, every import statement at any depth (NAME: the
    file's own module name, for its relative imports)."""
    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    is_pkg = os.path.basename(path) == "__init__.py"
    base = (name if is_pkg else name.rsplit(".", 1)[0]) if name else ""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [(a.name, ()) for a in node.names if a.name.split(".")[0] == PKG]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if not base:
                    continue
                b = base.split(".")
                b = b[: len(b) - (node.level - 1)] if node.level > 1 else b
                mod = ".".join(b + ([node.module] if node.module else []))
            else:
                mod = node.module or ""
            if mod.split(".")[0] == PKG:
                out.append((mod, tuple(a.name for a in node.names)))
    return out


def py_closure(src, entries):
    """{module name: file} of ENTRIES (module names, or paths of files outside the package) and all
    they import; the names that do not resolve."""
    seen, missing, todo = {}, set(), []
    for e in entries:
        if e.endswith(".py"):
            todo += [m for m, _ in imports_of(e)] + [f"{m}.{n}" for m, ns in imports_of(e) for n in ns]
        else:
            todo.append(e)
    while todo:
        name = todo.pop()
        if name in seen or name in missing:
            continue
        f = _module_file(src, name)
        if f is None:
            # (from package import Name: Name is not a module)
            if name.rsplit(".", 1)[0] in seen or _module_file(src, name.rsplit(".", 1)[0]):
                todo.append(name.rsplit(".", 1)[0])
            else:
                missing.add(name)
            continue
        seen[name] = f
        parts = name.split(".")
        todo += [".".join(parts[:i]) for i in range(1, len(parts))]
        for mod, names in imports_of(f, name):
            todo.append(mod)
            todo += [f"{mod}.{n}" for n in names if _module_file(src, f"{mod}.{n}")]
    missing = {m for m in missing if not _module_file(src, m.rsplit(".", 1)[0])}
    return seen, sorted(missing)


# ------------------------------------------------------------------------------------- the page
RX_HTML = re.compile(r"""<(?:link|script|img)\b[^>]*?\b(?:href|src)\s*=\s*["']([^"'#?]+)""", re.I)
RX_JS = re.compile(r"""(?:\bimport\s*(?:[\w*{}\s,$]+\bfrom\s*)?|\bexport\s*[\w*{}\s,$]+\bfrom\s*|\bimport\s*\(\s*)["']([^"']+)["']""")
RX_CSS = re.compile(r"""(?:@import\s+(?:url\()?|url\()\s*["']?([^"')\s]+)""")


def web_closure(src):
    """The files the app's page loads: {url path: file}, and the urls that resolve to no file.  The app
    serves /__o77w/studio/X from the Studio's web folder and /__o77w/X from the Warden's."""
    warden = os.path.join(src, PKG, "warden", "web")
    studio = os.path.join(src, PKG, "studio", "web")

    def file_of(url):
        if url.startswith("/studio/"):
            return os.path.join(studio, url[len("/studio/"):])
        return os.path.join(warden, url.lstrip("/"))

    def join(base_url, ref):
        if re.match(r"^[a-z]+:|^//|^data:", ref, re.I):
            return None
        parts = (ref if ref.startswith("/") else base_url.rsplit("/", 1)[0] + "/" + ref).split("/")
        out = []
        for p in parts:
            if p in ("", "."):
                continue
            if p == "..":
                if out:
                    out.pop()
                continue
            out.append(p)
        return "/" + "/".join(out)

    seen, missing, todo = {}, [], ["/index.html", "/capture.js"]
    while todo:
        url = todo.pop()
        if url in seen:
            continue
        f = file_of(url)
        if not os.path.isfile(f):
            missing.append(url)
            continue
        seen[url] = f
        if f.endswith((".html", ".js", ".css")):
            text = open(f, encoding="utf-8", errors="replace").read()
            rx = RX_HTML if f.endswith(".html") else RX_JS if f.endswith(".js") else RX_CSS
            for ref in rx.findall(text):
                u = join(url, ref)
                if u:
                    todo.append(u)
    return seen, missing


# ------------------------------------------------------------------------------------- texts
def warden_changelog(src):
    """The Warden's own entries of the changelog."""
    text = open(os.path.join(src, "CHANGELOG.md"), encoding="utf-8").read()
    parts = re.split(r"(?m)^(?=## )", text)
    keep = [p.rstrip() + "\n" for p in parts if p.startswith("## Open77 Warden")]
    return "# Changelog\n\n" + "\n".join(keep)


README = """# Open77 Warden

Your Open77 servers' **Warden** panels in one Windows app, each beside a live **3D map of Night City**
(the game's own buildings, props, streets and terrain, read from your installed Cyberpunk 2077) with the
server's players on it as holograms, updated every few seconds: their health, ping and state, a trail of
where they have been, follow one with the camera, and Warden's own actions from the map (heal or revive,
freeze, move -- to a spot you click, beside another player, out of a car -- warn, kick), each confirmed
first and done by Warden with your own sign-in and role, and written in Warden's audit.

**Download:** the setup is on the [Releases]({repo}/releases) page (`open77-warden-{version}-setup.exe`).

## Install and start

Run the setup: it installs per user (no admin) to `%LOCALAPPDATA%\\Programs\\open77-warden` with a Start
menu and desktop shortcut; a newer setup replaces an older install (closing the app first). Remove it in
Settings > Apps. Windows 10 or 11 with the Microsoft Edge WebView2 runtime (part of Windows 11).

    open77-warden.exe             the app window
    open77-warden.exe --browser   the same in your web browser

Data: `%LOCALAPPDATA%\\open77-warden` (the servers added in the app, the 3D city read from your game, logs).

## Servers

The servers set up in fivem2open77 Studio (Settings > Servers) are used as they are, with their SSH keys
or passwords; more can be added in the app. Each server's Warden is reached through its SSH connection,
as `ssh -L` would, at an address of its own on this PC: nothing is installed or changed on the server.
Warden has to be on in the server's `server.jsonc`:

```jsonc
"warden": { "enabled": true, "listenUrl": "http://127.0.0.1:11780" }
```

You sign in to Warden itself, in the panel beside the map: the map shows what your Warden role may see
and do. A server's first-run setup (its owner account and the one-time setup PIN printed in the server's
log at each start) is done in the same panel.

## The 3D city

Read once from your Cyberpunk 2077 install (about a minute), then kept in the app's data. The game's
files are never shipped with the app.

## Repository

| | |
|---|---|
| `fivem2open77/warden/` | the app: its local web server (`server.py`, `routes.py`), each server's panel as a site of its own on this PC (`proxy.py`), the SSH connections (`tunnel.py`), the window's pages (`web/`) |
| `host-warden/` | the Windows window (WebView2) and its per-user installer (C#, .NET Framework 4.8) |
| `host/` | the three C# files the window shares with the fivem2open77 Studio (start screen, icon, job object) |
| `fivem2open77/studio/`, `world/`, `cp77/`, ... | what the app shares with [fivem2open77 Studio](https://github.com/nobody71004/fivem2open77): SSH (`studio/deploy.py`), the 3D city (`world/citymap.py`, `studio/worldops.py`), the game's archives (`cp77/`), the Studio's 3D map page (`studio/web/js/pages/city3d.js`, `cityplayers.js`) -- the modules the app imports and nothing else |
| `tests/` | the app end to end against a stand-in Warden over a real SSH server (`fake_warden.py`, `sshd_fixture.py`), in Chromium (Playwright) |
| `tools/package/build_warden.py` | the app folder and the one-file setup |

## Build

The window (any OS with the .NET 8 SDK; it targets .NET Framework 4.8 through reference assemblies):

    dotnet build host-warden/Open77Warden.csproj -c Release -o out/host

The setup bundles the runtimes and tools of a fivem2open77 Studio app folder (Python 3.12 with numpy and
paramiko, the .NET runtime and WolvenKit CLI): the Studio's own build or an installed Studio
(`%LOCALAPPDATA%\\Programs\\fivem2open77`). With 7-Zip on PATH, `pip install pefile`, and the LZMA SDK
archive (`lzma*.7z`, for the setup's stub):

    python tools/package/build_warden.py --from-app <Studio app folder> --host out/host --sfx-sdk lzma2301.7z --out out

## Tests

    pip install -r requirements-dev.txt && python -m playwright install chromium
    python -m pytest tests

The SSH tests need OpenSSH's `sshd` (Linux, or WSL).

## Licence

GPL-3.0 (`LICENSE`), as fivem2open77. Bundled: three.js (MIT, `fivem2open77/studio/web/vendor/three/LICENSE`),
IBM Plex fonts (OFL, `fivem2open77/studio/web/fonts/OFL-IBM-Plex.txt`).
"""

GITIGNORE = """__pycache__/
*.pyc
.pytest_cache/
out/
host-warden/bin/
host-warden/obj/
*.log
"""

REQUIREMENTS = "numpy>=1.24\nparamiko>=3.4\n"
REQUIREMENTS_DEV = "-r requirements.txt\npytest>=8\nplaywright>=1.45\nPillow>=10\npefile>=2023.2.7\n"

TEST_REPO = '''"""The repository holds everything the app needs: every fivem2open77 module imported anywhere (at any depth,
lazily or not) is here, every file the app's page loads is here, and the C# files the window links."""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools", "package"))

import export_warden_repo as X  # noqa: E402


def test_every_import_is_in_the_repo():
    entries = [os.path.join(dp, f) for top in ("fivem2open77", "tests", "tools")
               for dp, _, fs in os.walk(os.path.join(ROOT, top)) for f in fs if f.endswith(".py")]
    names = []
    for f in entries:
        rel = os.path.relpath(f, ROOT)
        if rel.startswith("fivem2open77"):
            names.append(rel[:-3].replace(os.sep, ".").removesuffix(".__init__"))
    seen, missing = X.py_closure(ROOT, names + [f for f in entries if not os.path.relpath(f, ROOT).startswith("fivem2open77")])
    assert not missing, missing


def test_every_file_the_page_loads_is_in_the_repo():
    seen, missing = X.web_closure(ROOT)
    assert not missing, missing
    assert "/studio/js/pages/city3d.js" in seen and "/studio/vendor/three/three.module.min.js" in seen


def test_the_window_s_linked_sources_are_here():
    proj = open(os.path.join(ROOT, "host-warden", "Open77Warden.csproj"), encoding="utf-8").read()
    links = re.findall(r'Compile Include="([^"]+)"', proj)
    assert links
    for l in links:
        assert os.path.isfile(os.path.join(ROOT, "host-warden", l.replace("\\\\", os.sep))), l
'''


# ------------------------------------------------------------------------------------- export
def export(src, out, log=print):
    src = os.path.abspath(src)
    out = os.path.abspath(out)
    if os.path.exists(out) and os.listdir(out):
        raise ExportError(f"{out} is not empty")
    os.makedirs(out, exist_ok=True)

    def put(rel, from_path=None, text=None):
        dst = os.path.join(out, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if text is not None:
            with open(dst, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        else:
            shutil.copy2(from_path, dst)

    version = re.search(r'VERSION = "([^"]+)"', open(os.path.join(src, PKG, "warden", "__init__.py")).read()).group(1)

    # the tests (and the fixtures they import) and the tools: they name modules too
    tests = [t for t in TESTS if os.path.isfile(os.path.join(src, "tests", t))]
    local = set()
    todo = list(tests)
    while todo:
        t = todo.pop()
        if t in local:
            continue
        local.add(t)
        text = open(os.path.join(src, "tests", t), encoding="utf-8").read()
        for m in re.findall(r"(?m)^\s*(?:import|from)\s+([A-Za-z_]\w*)", text):
            if os.path.isfile(os.path.join(src, "tests", m + ".py")):
                todo.append(m + ".py")
    tools = [t for t in TOOLS if os.path.isfile(os.path.join(src, t))]

    # the Python package: the app's modules, and all they and the tests and tools import
    wpkg = os.path.join(src, PKG, "warden")
    entries = [f"{PKG}.warden" + ("" if f == "__init__.py" else "." + f[:-3]) for f in os.listdir(wpkg) if f.endswith(".py")]
    entries += [os.path.join(src, "tests", t) for t in sorted(local)] + [os.path.join(src, t) for t in tools]
    mods, missing = py_closure(src, entries)
    if missing:
        raise ExportError(f"imported but not found: {', '.join(missing)}")
    for name, f in sorted(mods.items()):
        put(os.path.relpath(f, src), f)
    data = os.path.join(src, PKG, "data")
    if os.path.isdir(data):
        shutil.copytree(data, os.path.join(out, PKG, "data"), ignore=SKIP)

    # the page: the Warden's web folder whole, and the Studio's web files it loads
    shutil.copytree(os.path.join(src, PKG, "warden", "web"), os.path.join(out, PKG, "warden", "web"), ignore=SKIP)
    pages, wmissing = web_closure(src)
    if wmissing:
        raise ExportError(f"the page loads files that are not there: {', '.join(wmissing)}")
    studio_web = os.path.join(src, PKG, "studio", "web")
    n_web = 0
    for url, f in sorted(pages.items()):
        if url.startswith("/studio/"):
            put(os.path.relpath(f, src), f)
            n_web += 1
    for rel in WEB_EXTRA:
        if os.path.isfile(os.path.join(studio_web, rel)):
            put(os.path.join(PKG, "studio", "web", rel), os.path.join(studio_web, rel))

    # the window: host-warden whole, and what it links from the Studio's host/
    shutil.copytree(os.path.join(src, "host-warden"), os.path.join(out, "host-warden"), ignore=SKIP)
    proj = open(os.path.join(src, "host-warden", "Open77Warden.csproj"), encoding="utf-8").read()
    for link in re.findall(r'Compile Include="([^"]+)"', proj):
        f = os.path.normpath(os.path.join(src, "host-warden", link.replace("\\", os.sep)))
        if not os.path.isfile(f):
            raise ExportError(f"host-warden links {link}, which is not there")
        put(os.path.relpath(f, src), f)

    for t in sorted(local):
        put(os.path.join("tests", t), os.path.join(src, "tests", t))
    for t in tools:
        put(t, os.path.join(src, t))
    put(os.path.join("tools", "package", "export_warden_repo.py"), os.path.abspath(__file__))
    put(os.path.join("tests", "test_repo.py"), text=TEST_REPO)

    put("README.md", text=README.replace("{repo}", REPO_URL).replace("{version}", version))
    put("CHANGELOG.md", text=warden_changelog(src))
    put("LICENSE", os.path.join(src, "LICENSE"))
    put(".gitignore", text=GITIGNORE)
    put("requirements.txt", text=REQUIREMENTS)
    put("requirements-dev.txt", text=REQUIREMENTS_DEV)

    n = sum(len(fs) for _, _, fs in os.walk(out))
    log(f"Open77 Warden {version} -> {out}: {n} files ({len(mods)} Python modules, {n_web} Studio web files, "
        f"{len(local)} test files)")
    return {"version": version, "files": n, "modules": sorted(mods), "web": sorted(pages)}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("out")
    p.add_argument("--source", default=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    a = p.parse_args(argv)
    try:
        export(a.source, a.out)
    except ExportError as e:
        print(f"export failed: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

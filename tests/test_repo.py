"""The repository holds everything the app needs: every fivem2open77 module imported anywhere (at any depth,
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
        assert os.path.isfile(os.path.join(ROOT, "host-warden", l.replace("\\", os.sep))), l

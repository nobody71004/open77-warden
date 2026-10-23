# Open77 Warden

Your Open77 servers' **Warden** panels in one Windows app, each beside a live **3D map of Night City**
(the game's own buildings, props, streets and terrain, read from your installed Cyberpunk 2077) with the
server's players on it as holograms, updated every few seconds: their health, ping and state, a trail of
where they have been, follow one with the camera, and Warden's own actions from the map (heal or revive,
freeze, move -- to a spot you click, beside another player, out of a car -- warn, kick), each confirmed
first and done by Warden with your own sign-in and role, and written in Warden's audit.

**Download:** the setup is on the [Releases](https://github.com/nobody71004/open77-warden/releases) page (`open77-warden-1.0.1-setup.exe`).

## Install and start

Run the setup: it installs per user (no admin) to `%LOCALAPPDATA%\Programs\open77-warden` with a Start
menu and desktop shortcut; a newer setup replaces an older install (closing the app first). Remove it in
Settings > Apps. Windows 10 or 11 with the Microsoft Edge WebView2 runtime (part of Windows 11).

    open77-warden.exe             the app window
    open77-warden.exe --browser   the same in your web browser

Data: `%LOCALAPPDATA%\open77-warden` (the servers added in the app, the 3D city read from your game, logs).

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
(`%LOCALAPPDATA%\Programs\fivem2open77`). With 7-Zip on PATH, `pip install pefile`, and the LZMA SDK
archive (`lzma*.7z`, for the setup's stub):

    python tools/package/build_warden.py --from-app <Studio app folder> --host out/host --sfx-sdk lzma2301.7z --out out

## Tests

    pip install -r requirements-dev.txt && python -m playwright install chromium
    python -m pytest tests

The SSH tests need OpenSSH's `sshd` (Linux, or WSL).

## Licence

GPL-3.0 (`LICENSE`), as fivem2open77. Bundled: three.js (MIT, `fivem2open77/studio/web/vendor/three/LICENSE`),
IBM Plex fonts (OFL, `fivem2open77/studio/web/fonts/OFL-IBM-Plex.txt`).

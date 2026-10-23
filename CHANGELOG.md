# Changelog

## Open77 Warden 1.0.1 (2026-10-23)

* **Warden's first-run setup can be scrolled.** Warden's pages do not scroll their body (the panel scrolls
  its own views), and its first-run page (the setup PIN, the owner's name and password) is a column
  centred in a box as tall as the window: in a smaller window the end of the form -- the confirm-password
  box and the button -- was cut off with no way to reach it. When a Warden page is taller than the app's
  panel and cannot scroll, the app now lets it scroll, inside the panel and up and down only; once it fits
  again (Warden's own panel after the setup) it is left exactly as Warden made it.
* **SSH reads no longer lose the end of a long output** (shared with the Studio, in its next release too).
  A command's output is read until the server says it has ended, not only until the command's exit
  status arrived: the server can send the exit status first, while the end of a long output is still on
  its way. The Studio's server-log reads lost their newest lines that way, and an old Warden setup PIN was
  read from a log whose last start never arrived.
* Open77 Warden has a repository of its own: `nobody71004/open77-warden`.

## Open77 Warden 1.0.0 (2026-10-22)

A new app beside the Studio: each Open77 server's own Warden panel with a live 3D map of its players.

* Its own setup (`open77-warden-1.0.0-setup.exe`) and install (`%LOCALAPPDATA%\Programs\open77-warden`),
  with the Studio's runtimes and its own data and 3D city cache (`%LOCALAPPDATA%\open77-warden`).
* Servers from the Studio's settings (with their SSH keys and passwords) or added in the app. Each
  server's Warden is reached through SSH, as `ssh -L` would, at an address of its own on this PC;
  nothing changes on the server (Warden has to be on in its `server.jsonc`).
* The live map: the Studio's 3D city with the game's own meshes, the players as holograms from
  Warden's roster every 2 seconds (state colours, labels with ping and health, a 30-second trail,
  smooth movement), a click for the player's card, *Follow* and *Fly to*.
* Warden's actions from the map, each confirmed and done by Warden with your own sign-in and role:
  heal/revive, freeze/thaw, move (to a spot clicked on the map, to coordinates, beside a player,
  bring a player; a driver taken out of the car first when asked), warn, kick.

### Shared with the Studio's Live maps

* The districts' glowing outlines were not drawn (their ribbons faced away from the camera, and the
  back faces were culled): they show again. The same fix makes the player trails show.
* Players online are holograms now (the Warden's player layer), with labels that move out of each
  other's way.

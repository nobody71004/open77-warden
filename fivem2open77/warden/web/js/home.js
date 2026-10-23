// The home page: the servers (the Studio's, and any added here), each one's Warden as the app can
// reach it, the game folder Night City is read from, and the log.
import { api } from "../studio/js/api.js";
import { h, clear, toast, modal } from "../studio/js/ui.js";

export async function homePage(root, state, { auto } = {}) {
  document.title = "Open77 Warden";
  // straight to the server shown last (the window opens here every time)
  if (auto && state.last && state.targets.some((t) => t.key === state.last)) {
    try {
      const r = await api.get(`/go/${encodeURIComponent(state.last)}`);
      location.replace(r.url);
      return;
    } catch (_) { /* stay here */ }
  }
  const list = h("div.h-list");
  const top = h("header.w-top", h("div.w-brand", h("img", { src: "img/icon-32.png", alt: "" }), h("span", "OPEN//77"), h("b", "WARDEN")), h("div.w-status.ok", h("i.dot"), h("span", `version ${state.version}`)),
    h("div.w-right", h("button.w-btn", { type: "button", onclick: () => openSettings() }, "Settings")));
  const intro = h("p.h-intro", "Each Open77 server's own Warden panel, reached through the server's SSH connection, with a live map of Night City ",
    "(the game's own meshes, read from your game) and the players on it as holograms.");
  const studioNote = h("p.h-note", state.studio.present
    ? ["Servers from fivem2open77 Studio (", h("code", state.studio.workspace), ") and any you add here."]
    : "fivem2open77 Studio is not on this PC: add your servers here (the same form as the Studio's).");
  const addBtn = h("button.w-btn.primary", { type: "button", onclick: () => editServer(null) }, "Add a server");
  const page = h("main.h-main", h("div.h-in", h("h1", "Servers"), intro, studioNote, list, h("div.h-actions", addBtn),
    h("h2", "Night City"), gameBox(state)));
  root.append(top, page);
  render(state);
  if (location.hash.includes("settings")) {               // (a server page's "Open Settings")
    history.replaceState(null, "", "#stay");
    openSettings();
  }

  function render(st) {
    clear(list);
    if (!st.targets.length) list.append(h("div.h-empty", "No server yet. Add one (or set it up in the Studio's Settings > Servers)."));
    for (const t of st.targets) list.append(card(t));
  }

  function card(t) {
    const stEl = h("div.h-state");
    const show = (x) => {
      clear(stEl);
      if (x.error) stEl.append(h("span.bad", x.error));
      else if (x.warden && !x.warden.enabled) stEl.append(h("span.warn", "Warden is off on this server"));
      else if (x.warden) stEl.append(...[h("span.ok", `Warden on ${x.warden.listenUrl}`), x.ssh && x.ssh.connected ? h("span.ok", " · SSH connected") : null].filter(Boolean));
      else stEl.append(h("span", "not checked yet"));
    };
    show(t);
    const check = async () => {
      clear(stEl).append(h("span", "checking…"));
      try { show(await api.post(`/targets/${encodeURIComponent(t.key)}/check`)); } catch (e) { show({ error: e.message }); }
    };
    const open = async () => {
      try { const r = await api.get(`/go/${encodeURIComponent(t.key)}`); location.href = r.url; } catch (e) { toast(e.message, "err"); }
    };
    const c = h("div.h-card", h("div.h-head", h("b", t.display || t.name), h("span.h-src", t.source === "studio" ? "from the Studio" : "added here")),
      h("div.h-sub", `${t.server}: ${t.ssh.user}@${t.ssh.host}:${t.ssh.port} · ${t.folder}`), stEl,
      h("div.h-btns", h("button.w-btn.primary", { type: "button", onclick: open }, "Open"), h("button.w-btn", { type: "button", onclick: check }, "Check"),
        t.source === "warden" ? h("button.w-btn", { type: "button", onclick: () => editServer(t.server) }, "Edit") : null));
    check();
    return c;
  }

  function gameBox(st) {
    const box = h("div.h-game");
    const fill = async () => {
      clear(box);
      let cm = null;
      try { cm = await api.get("/citymap"); } catch (e) { box.append(h("p.bad", e.message)); return; }
      box.append(h("div.h-sub", st.game.dir ? ["Read from ", h("code", st.game.dir)] : h("span.warn", "Cyberpunk 2077 was not found: set its folder in Settings.")),
        h("div.h-sub", cm.ready ? `The 3D city is ready (${Object.entries(cm.stats || {}).map(([k, v]) => `${k} ${v}`).join(", ")}).`
          : "The 3D city is read from the game the first time a map is opened (about a minute)."));
    };
    fill();
    return box;
  }

  async function openSettings() {
    const s = await api.get("/settings");
    const game = h("input", { type: "text", value: s.paths.game_dir, placeholder: state.game.dir || "C:\\...\\Cyberpunk 2077" });
    const useStudio = h("input", { type: "checkbox", checked: s.studio.use });
    const poll = h("input", { type: "number", min: 1, max: 30, step: 0.5, value: s.poll_seconds });
    const trail = h("input", { type: "number", min: 0, max: 600, step: 5, value: s.trail_seconds });
    const ok = await modal("Settings", h("div.h-form",
      h("label.w-field", h("span", "Cyberpunk 2077 folder (empty: found automatically)"), game),
      h("label.w-check", useStudio, h("span", "Use the servers of fivem2open77 Studio")),
      h("label.w-field", h("span", "Ask Warden for the players every (seconds)"), poll),
      h("label.w-field", h("span", "Trail behind each player (seconds, 0: none)"), trail)),
    [{ label: "Cancel", value: false }, { label: "Save", value: true, kind: "primary" }]);
    if (ok !== true) return;
    try {
      await api.patch("/settings", { paths: { game_dir: game.value.trim() }, studio: { use: useStudio.checked },
        poll_seconds: Number(poll.value), trail_seconds: Number(trail.value) });
      location.reload();
    } catch (e) { toast(e.message, "err"); }
  }

  async function editServer(sid) {
    const s = await api.get("/settings");
    const cur = s.servers.find((x) => x.id === sid) || { id: "", label: "", host: "", port: 22, user: "root", auth: "key", key_file: "", servers_root: "/opt", names: [] };
    const f = {
      id: h("input", { type: "text", value: cur.id, placeholder: "vps1", disabled: !!sid }),
      label: h("input", { type: "text", value: cur.label, placeholder: "My VPS" }),
      host: h("input", { type: "text", value: cur.host, placeholder: "203.0.113.10" }),
      port: h("input", { type: "number", value: cur.port }),
      user: h("input", { type: "text", value: cur.user }),
      auth: h("select", ["key", "password", "agent"].map((a) => h("option", { value: a, selected: a === cur.auth }, a))),
      key_file: h("input", { type: "text", value: cur.key_file, placeholder: "C:\\Users\\you\\.ssh\\id_ed25519" }),
      password: h("input", { type: "password", placeholder: cur.password_set ? "(saved: type to change)" : "" }),
      servers_root: h("input", { type: "text", value: cur.servers_root }),
      names: h("input", { type: "text", value: (cur.names || []).join(", "), placeholder: "open77-xbuniverse, open77-staging" }),
    };
    const row = (label, el) => h("label.w-field", h("span", label), el);
    const ok = await modal(sid ? `Server ${sid}` : "Add a server", h("div.h-form",
      row("Id (a-z 0-9 _ -)", f.id), row("Name", f.label), row("SSH host", f.host), row("SSH port", f.port), row("User", f.user),
      row("Sign-in", f.auth), row("Private key file (key)", f.key_file), row("Password (password) or key passphrase", f.password),
      row("Folder the Open77 servers are in", f.servers_root), row("Server folders (comma-separated)", f.names)),
    [sid ? { label: "Remove", value: "remove", kind: "danger" } : null, { label: "Cancel", value: false }, { label: "Save", value: true, kind: "primary" }].filter(Boolean));
    if (ok === "remove") {
      try { await api.del(`/servers/${sid}`); location.reload(); } catch (e) { toast(e.message, "err"); }
      return;
    }
    if (ok !== true) return;
    const body = { id: f.id.value.trim(), label: f.label.value.trim(), host: f.host.value.trim(), port: Number(f.port.value) || 22,
      user: f.user.value.trim(), auth: f.auth.value, key_file: f.key_file.value.trim(), servers_root: f.servers_root.value.trim(),
      names: f.names.value.split(",").map((x) => x.trim()).filter(Boolean) };
    if (f.password.value) body[body.auth === "password" ? "password" : "passphrase"] = f.password.value;
    try {
      await api.post("/servers", body);
      location.reload();
    } catch (e) {
      toast(e.body && e.body.errors ? e.body.errors.join("; ") : e.message, "err");
    }
  }
}

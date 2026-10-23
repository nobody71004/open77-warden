// One server's page: the live map (Night City in 3D with the players as holograms, from Warden's
// roster every few seconds), the server's own Warden panel beside it or instead of it, the roster,
// and a player's card with Warden's actions (each confirmed first, each in Warden's audit).
import { api, waitJob } from "../studio/js/api.js";
import { h, clear, toast, modal } from "../studio/js/ui.js";
import { cityStage } from "../studio/js/pages/city3d.js";
import * as W from "./warden.js";
import { normalize, shapeOf } from "./roster.js";

const VIEWS = { map: "Live map", split: "Map + panel", panel: "Warden panel" };
const VIEW_KEY = "o77w.view";

function plog(text, level = "info") {
  api.post("/log", { text, level }).catch(() => { /* the app is gone */ });
}
const fmtAge = (s) => (s < 1.5 ? "now" : s < 90 ? `${Math.round(s)} s ago` : `${Math.round(s / 60)} min ago`);
const fmtPos = (p) => (Number.isFinite(p.x) ? `${p.x.toFixed(1)}, ${p.y.toFixed(1)}, ${(p.z || 0).toFixed(1)}` : "no position yet");

export function panelPage(root, state) {
  const here = state.targets.find((t) => t.key === state.here);
  const title = here ? (here.display || here.name) : state.here;
  document.title = `${title} · Open77 Warden`;
  let view = (() => { try { return localStorage.getItem(VIEW_KEY) || "map"; } catch (_) { return "map"; } })();
  if (!VIEWS[view]) view = "map";

  // ---------------------------------------------------------------- the frame of the page
  const serverSel = h("select.w-server", { title: "The server whose panel and players this window shows" },
    state.targets.map((t) => h("option", { value: t.key, selected: t.key === state.here }, `${t.display || t.name} — ${t.label}`)));
  serverSel.addEventListener("change", () => go(serverSel.value));
  const statusEl = h("div.w-status", h("i.dot"), h("span", "connecting"));
  const tabs = h("nav.w-tabs", Object.entries(VIEWS).map(([k, label]) => h("button", { type: "button", "data-v": k, onclick: () => setView(k) }, label)));
  const logBtn = h("button.w-btn", { type: "button", onclick: () => openLog() }, "Log");
  const homeBtn = h("button.w-btn", { type: "button", onclick: () => go("home") }, "Servers");
  const top = h("header.w-top", h("div.w-brand", h("img", { src: "img/icon-32.png", alt: "" }), h("span", "OPEN//77"), h("b", "WARDEN")), serverSel, statusEl, tabs, h("div.w-right", logBtn, homeBtn));
  const mapBox = h("section.w-map");
  const frame = h("iframe.w-frame", { src: "/", title: "Warden", referrerpolicy: "same-origin" });
  W.setPanelFrame(frame);
  const panelBox = h("section.w-panel", frame);
  const main = h("main.w-main", mapBox, panelBox);
  const banner = h("div.w-banner.hidden");
  root.append(top, main);
  mapBox.append(banner);
  frame.addEventListener("load", () => { plog(`the panel frame loaded ${safePath(frame)}`); poll(true); });

  function safePath(f) { try { return f.contentWindow.location.pathname; } catch (_) { return "(elsewhere)"; } }

  function setView(k) {
    view = k;
    try { localStorage.setItem(VIEW_KEY, k); } catch (_) { /* */ }
    main.className = `w-main view-${k}`;
    for (const b of tabs.children) b.classList.toggle("on", b.dataset.v === k);
    window.dispatchEvent(new Event("resize"));
  }
  setView(view);

  async function go(key, page) {
    try {
      const pg = page || (key === "home" ? "stay" : "");
      const r = await api.get(`/go/${encodeURIComponent(key)}${pg ? `?page=${encodeURIComponent(pg)}` : ""}`);
      location.href = r.url;
    } catch (e) {
      toast(e.message, "err");
    }
  }

  // ---------------------------------------------------------------- the map
  const players = new Map();          // key -> normalized player
  let selectedKey = null;
  let followKey = null;
  const stage = cityStage(mapBox, {
    onPlayer: (p) => select(p.key),
    onFollow: (k) => { followKey = k; renderRoster(); renderCard(); },
    appName: "Open77 Warden",
    autoBuild: true,                                         // (the first time: no button to find)
    onSettings: () => go("home", "stay/settings"),           // (the game folder is set on the home page)
  });

  // ---------------------------------------------------------------- the roster panel
  const search = h("input.w-search", { type: "search", placeholder: "Find a player", spellcheck: false });
  const countEl = h("span.w-count", "-");
  const ageEl = h("span.w-age", "");
  const list = h("div.w-list");
  const rosterEl = h("aside.w-roster", h("div.w-rhead", h("b", "PLAYERS"), countEl, ageEl,
    h("button.w-icon", { type: "button", title: "Hide the list", onclick: () => rosterEl.classList.toggle("folded") }, "‹")), search, list);
  mapBox.append(rosterEl);
  search.addEventListener("input", () => renderRoster());

  function stateClass(p) {
    for (const s of ["dead", "frozen", "vehicle", "god", "watching", "ghost", "hidden", "loading", "notready"]) if ((p.states || []).includes(s)) return s;
    return "alive";
  }
  function hpPct(p) {
    if (!Number.isFinite(p.health)) return null;
    const max = Number.isFinite(p.healthMax) && p.healthMax > 0 ? p.healthMax : 100;
    return Math.max(0, Math.min(100, p.health / max * 100));
  }
  // one row per player, kept from poll to poll and changed in place (a click or a double-click
  // that straddles a refresh still lands on its row)
  const rows = new Map();             // key -> { el, nm, fl, pg, hp, hpi }
  const emptyEl = h("div.w-empty");
  function rowFor(p) {
    let r = rows.get(p.key);
    if (!r) {
      const key = p.key;
      const nm = h("span.nm"), fl = h("em.fl", "FOLLOW"), pg = h("span.pg"), hpi = h("i"), hp = h("i.hp", hpi);
      const el = h("button.w-row", { type: "button", onclick: () => select(key), ondblclick: () => stage.follow(key) }, h("i.st"), nm, fl, pg, hp);
      r = { el, nm, fl, pg, hp, hpi };
      rows.set(key, r);
    }
    const pct = hpPct(p);
    const cls = `w-row st-${stateClass(p)}${p.key === selectedKey ? " sel" : ""}`;
    if (r.el.className !== cls) r.el.className = cls;
    const name = p.name || `player ${p.id}`;
    if (r.nm.textContent !== name) { r.nm.textContent = name; r.el.title = `${name} — double-click to follow`; }
    r.fl.hidden = p.key !== followKey;
    const ping = Number.isFinite(p.ping) ? `${Math.round(p.ping)} ms` : "—";
    if (r.pg.textContent !== ping) r.pg.textContent = ping;
    r.hp.style.visibility = pct == null ? "hidden" : "";
    if (pct != null) r.hpi.style.width = `${pct.toFixed(0)}%`;
    return r.el;
  }
  function renderRoster() {
    const q = search.value.trim().toLowerCase();
    const items = [...players.values()].filter((p) => !q || (p.name || "").toLowerCase().includes(q) || String(p.id).includes(q))
      .sort((a, b) => String(a.name || "").localeCompare(String(b.name || "")) || String(a.key).localeCompare(String(b.key)));
    for (const [k, r] of rows) if (!players.has(k)) { r.el.remove(); rows.delete(k); }
    const want = items.map(rowFor);
    const keep = new Set(want);
    for (const el of [...list.children]) if (!keep.has(el)) el.remove();
    want.forEach((el, i) => { if (list.children[i] !== el) list.insertBefore(el, list.children[i] || null); });
    if (!items.length) {
      emptyEl.textContent = players.size ? "No player by that name" : lastOk ? "Nobody is on the server" : "—";
      list.append(emptyEl);
    }
    countEl.textContent = String(players.size);
  }

  // ---------------------------------------------------------------- the player's card
  const card = h("div.w-card.hidden");
  mapBox.append(card);

  function select(key) {
    selectedKey = key && players.has(key) ? key : null;
    stage.select(selectedKey);
    renderRoster();
    renderCard();
  }

  // the card is built when the player or what its buttons offer changes; the poll then changes its
  // figures in place (a click on a button never meets a button just replaced)
  let cardSig = "", cardLive = null;
  function renderCard() {
    const p0 = selectedKey ? players.get(selectedKey) : null;
    card.classList.toggle("hidden", !p0);
    if (!p0) { cardSig = ""; cardLive = null; clear(card); return; }
    const key = p0.key;
    const cur = () => players.get(key) || p0;            // (the actions use the newest roster entry)
    const states = p0.states || [];
    const dead = states.includes("dead"), frozen = states.includes("frozen"), following = followKey === key;
    const sig = JSON.stringify([key, p0.name, p0.id, p0.identity, states, following, hpPct(p0) == null]);
    if (sig !== cardSig) {
      cardSig = sig;
      const meta = h("span"), pos = h("span"), age = h("em"), hpi = h("i"), hpText = h("span"), ar = h("span.ar");
      cardLive = { meta, pos, age, hpi, hpText, ar };
      clear(card).append(...[
        h("div.w-chead", h("i.st"), h("b", p0.name || `player ${p0.id}`),
          h("button.w-icon", { type: "button", title: "Close", onclick: () => select(null) }, "✕")),
        h("div.w-cmeta", `#${p0.id != null ? p0.id : "?"}`, p0.identity ? h("span", { title: String(p0.identity) }, ` · ${String(p0.identity).slice(0, 8)}…`) : null, meta),
        h("div.w-cpos", h("span", "POS"), pos, age),
        hpPct(p0) == null ? null : h("div.w-chp", h("span", "HP"), h("i.hp", hpi), hpText, ar),
        states.length ? h("div.w-cchips", states.map((s) => h("u", s === "vehicle" ? "in vehicle" : s === "notready" ? "not ready" : s))) : null,
        h("div.w-cbtns",
          h("button.w-btn", { type: "button", onclick: () => stage.follow(followKey === key ? null : key) }, following ? "Stop following" : "Follow"),
          h("button.w-btn", { type: "button", onclick: () => stage.flyToPlayer(key) }, "Fly to")),
        h("div.w-cbtns.act",
          h("button.w-btn.ok", { type: "button", onclick: () => doSimple(cur(), dead ? "revive" : "heal") }, dead ? "Revive" : "Heal"),
          h("button.w-btn.cool", { type: "button", onclick: () => doSimple(cur(), frozen ? "unfreeze" : "freeze") }, frozen ? "Thaw" : "Freeze"),
          h("button.w-btn", { type: "button", onclick: (e) => moveMenu(cur(), e.currentTarget) }, "Move ▾"),
          h("button.w-btn", { type: "button", onclick: () => doWarn(cur()) }, "Warn"),
          h("button.w-btn.bad", { type: "button", onclick: () => doKick(cur()) }, "Kick")),
      ].filter(Boolean));                                 // (append() would write a null as "null")
      card.dataset.state = stateClass(p0);
    }
    const p = p0, L = cardLive, pct = hpPct(p);
    const set = (el, text) => { if (el.textContent !== text) el.textContent = text; };
    set(L.meta, (p.bucket != null && p.bucket !== 0 ? ` · match ${p.bucket}` : " · lobby") + (Number.isFinite(p.ping) ? ` · ${Math.round(p.ping)} ms` : ""));
    set(L.pos, fmtPos(p));
    set(L.age, Number.isFinite(p.age) ? ` ${fmtAge(p.age)}` : "");
    if (pct != null) {
      L.hpi.style.width = `${pct.toFixed(0)}%`;
      set(L.hpText, `${Math.round(p.health)}${Number.isFinite(p.healthMax) ? " / " + Math.round(p.healthMax) : "%"}`);
      set(L.ar, Number.isFinite(p.armor) ? ` armour ${Math.round(p.armor)}` : "");
    }
  }

  // ---------------------------------------------------------------- actions (each confirmed)
  async function run(p, action, values, what) {
    const res = await W.act(p.id, action, values).catch((e) => ({ ok: false, status: e.status || 0, message: e.message }));
    plog(`action ${action} on #${p.id} (${p.name || "?"}) -> ${res.status} ${res.ok ? "ok" : "refused"}: ${res.message}`
      + (res.sent ? ` [fields ${res.sent.join(", ") || "none"}]` : "")
      + (res.headers ? ` [headers ${res.headers.concat(res.forged || []).join(", ") || "none"}]` : ""), res.ok ? "info" : "warn");
    if (res.ok) toast(`${what}: ${res.message}`);
    else if (res.forgery) {
      // Warden wants the anti-forgery proof its own page sends: the app learns it from the panel
      const again = await modal(`${what}: refused`, h("div",
        h("p", `Warden turned the call down as cross-site (${res.status}: ${res.message}).`),
        h("p", "The app sends Warden exactly what its own panel sends. Do any one action in the Warden panel "
          + "(a button on its Players tab), then try again here: from then on the map's actions go through.")),
      [{ label: "Close", value: false }, { label: "Show the panel", value: true, kind: "primary" }]);
      if (again === true && view === "map") setView("split");
    } else toast(`${what} refused by Warden${res.status && res.status !== 200 ? ` (${res.status})` : ""}: ${res.message}`, "err");
    poll(true);
    return res;
  }

  async function doSimple(p, action) {
    const label = W.ACTIONS[action].label;
    const text = {
      heal: `Restore ${p.name} to full health? Warden tells them an administrator did it.`,
      revive: `Bring ${p.name} back where they fell, at full health?`,
      freeze: `Hold ${p.name}'s body still? They keep their camera, chat and voice.`,
      unfreeze: `Release the panel's hold on ${p.name}? (A script that also holds them keeps them frozen.)`,
    }[action];
    const ok = await modal(`${label} ${p.name}`, h("p", text), [{ label: "Cancel", value: false }, { label, value: true, kind: "primary" }]);
    if (ok === true) await run(p, action, {}, `${label} ${p.name}`);
  }

  async function doKick(p) {
    const reason = h("input", { type: "text", value: "Kicked by an operator", maxLength: 200 });
    const ok = await modal(`Kick ${p.name}`, h("div", h("p", "They are disconnected (they can join again). The reason is shown to them."),
      h("label.w-field", h("span", "Reason"), reason)), [{ label: "Cancel", value: false }, { label: "Kick", value: true, kind: "danger" }]);
    if (ok === true) await run(p, "kick", { text: reason.value.trim() || "Kicked by an operator" }, `Kick ${p.name}`);
  }

  async function doWarn(p) {
    const msg = h("textarea", { rows: 3, maxLength: 400, placeholder: "The message they see" });
    const ok = await modal(`Warn ${p.name}`, h("div", h("p", "An admin message to this player only."), h("label.w-field", h("span", "Message"), msg)),
      [{ label: "Cancel", value: false }, { label: "Send", value: true, kind: "primary" }]);
    if (ok === true && msg.value.trim()) await run(p, "warn", { text: msg.value.trim() }, `Warn ${p.name}`);
  }

  function moveMenu(p, anchor) {
    const close = () => menu.remove();
    const menu = h("div.w-menu",
      h("button", { type: "button", onclick: () => { close(); moveToSpot(p); } }, "To a spot on the map…"),
      h("button", { type: "button", onclick: () => { close(); moveToCoords(p); } }, "To coordinates…"),
      h("button", { type: "button", onclick: () => { close(); moveToPlayer(p); } }, "Beside another player…"),
      h("button", { type: "button", onclick: () => { close(); bringHere(p); } }, `Bring another player to ${p.name}…`));
    const r = anchor.getBoundingClientRect(), mr = mapBox.getBoundingClientRect();
    menu.style.left = `${r.left - mr.left}px`;
    menu.style.bottom = `${mr.bottom - r.top + 4}px`;
    mapBox.append(menu);
    setTimeout(() => document.addEventListener("click", function off(e) { if (!menu.contains(e.target)) { close(); document.removeEventListener("click", off); } }), 0);
  }

  function vehicleBox(p) {
    const inVeh = (p.states || []).includes("vehicle");
    const cb = h("input", { type: "checkbox", checked: false });
    return { el: inVeh ? h("label.w-check", cb, h("span", "Take them out of the vehicle first (a move is refused while they drive)")) : null, cb };
  }

  async function confirmMove(p, where, values) {
    const vb = vehicleBox(p);
    const warn = (p.states || []).includes("dead") ? h("p.warn", `${p.name} is dead: Warden refuses to move a body.`) : null;
    const ok = await modal(`Move ${p.name}`, h("div", h("p", `Move ${p.name} ${where}? The game fades their screen, moves them and waits for them to stand on solid ground.`),
      warn, vb.el), [{ label: "Cancel", value: false }, { label: "Move", value: true, kind: "primary" }]);
    if (ok !== true) return;
    await run(p, "teleport", Object.assign({ leaveVehicle: vb.cb.checked }, values), `Move ${p.name}`);
  }

  function moveToSpot(p) {
    toast(`Click the city where ${p.name} should go (Esc: never mind)`);
    stage.pickOnce(`move ${p.name} here`, (at) => {
      if (!at) return;
      confirmMove(p, `to ${at.x.toFixed(1)}, ${at.y.toFixed(1)}, ${at.z.toFixed(1)}`, { x: at.x, y: at.y, z: at.z });
    });
  }

  async function moveToCoords(p) {
    const ins = ["x", "y", "z"].map((k, i) => h("input", { type: "number", step: "0.1", value: Number.isFinite(p[k]) ? p[k].toFixed(1) : "0" }));
    const ok = await modal(`Move ${p.name}`, h("div", h("p", "To these coordinates (their match stays the same). Their own position is filled in: a small nudge is a small edit."),
      h("div.w-xyz", ins.map((e, i) => h("label.w-field", h("span", "XYZ"[i]), e)))), [{ label: "Cancel", value: false }, { label: "Next", value: true, kind: "primary" }]);
    if (ok !== true) return;
    const [x, y, z] = ins.map((e) => Number(e.value));
    if (![x, y, z].every(Number.isFinite)) return toast("Coordinates: three numbers", "err");
    confirmMove(p, `to ${x.toFixed(1)}, ${y.toFixed(1)}, ${z.toFixed(1)}`, { x, y, z });
  }

  async function pickPlayer(title, exclude) {
    const others = [...players.values()].filter((o) => o.key !== exclude);
    if (!others.length) { toast("Nobody else is on the server"); return null; }
    const sel = h("select", others.map((o) => h("option", { value: o.key }, `${o.name || "?"} (#${o.id})`)));
    const ok = await modal(title, h("label.w-field", h("span", "Player"), sel), [{ label: "Cancel", value: false }, { label: "Next", value: true, kind: "primary" }]);
    return ok === true ? players.get(sel.value) : null;
  }

  async function moveToPlayer(p) {
    const t = await pickPlayer(`Move ${p.name} beside…`, p.key);
    if (t) confirmMove(p, `beside ${t.name} (into ${t.name}'s match)`, { target: t.id });
  }

  async function bringHere(p) {
    const q = await pickPlayer(`Bring to ${p.name}…`, p.key);
    if (q) confirmMove(q, `beside ${p.name} (into ${p.name}'s match)`, { target: p.id });
  }

  // ---------------------------------------------------------------- polling Warden's roster
  let lastOk = 0, lastShape = "", polling = false, timer = 0, failures = 0, signedOut = false;
  const every = Math.max(1, Number(state.poll_seconds) || 2) * 1000;

  function setStatus(kind, text) {
    statusEl.className = `w-status ${kind}`;
    statusEl.lastChild.textContent = text;
  }
  function showBanner(kind, text, action) {
    if (!kind) { banner.classList.add("hidden"); return; }
    clear(banner).append(...[h("span", text), action ? h("button.w-btn.primary", { type: "button", onclick: action.fn }, action.label) : null].filter(Boolean));
    banner.className = `w-banner ${kind}`;
  }

  async function poll(now) {
    if (polling) return;
    clearTimeout(timer);
    polling = true;
    try {
      const r = await W.roster();
      if (r.ok) {
        const shape = shapeOf(r.body);
        if (shape !== lastShape) { plog(`roster: ${shape}`); lastShape = shape; }
        const { players: list, unplaced, count } = normalize(r.body);
        players.clear();
        for (const p of list.concat(unplaced)) players.set(p.key, p);
        stage.update({ items: [], players: list, trailSeconds: Number(state.trail_seconds) || 30 });
        if (selectedKey && !players.has(selectedKey)) select(null);
        lastOk = Date.now();
        failures = 0;
        if (signedOut) { signedOut = false; plog("signed in to Warden: the roster answers"); }
        setStatus("ok", `${count} player${count === 1 ? "" : "s"} · Warden signed in`);
        showBanner(null);
        renderRoster();
        renderCard();
      } else {
        failures++;
        if (r.signedOut) {
          if (!signedOut) plog(`roster: not signed in (${r.status})`);
          signedOut = true;
          setStatus("warn", "sign in to Warden");
          showBanner("warn", "Sign in to Warden to see the players: the map shows them with your Warden role.",
            view === "map" ? { label: "Open the panel", fn: () => setView("split") } : null);
        } else if (r.forbidden) {
          setStatus("warn", "your Warden role cannot see players");
          showBanner("warn", "Signed in, but your Warden role does not hold players.view: an owner can grant it in Warden's Users.");
        } else if (r.unreachable) {
          setStatus("bad", "Warden not reachable");
          showBanner("bad", `Warden is not reachable: ${r.error}`, { label: "Try again", fn: () => { api.post(`/targets/${encodeURIComponent(state.here)}/check`).catch(() => {}); poll(true); } });
          if (failures === 1 || failures % 30 === 0) plog(`roster: unreachable: ${r.error}`, "warn");
        } else {
          setStatus("bad", `Warden answered ${r.status}`);
          showBanner("bad", `Warden's roster: ${r.error}`);
          if (failures === 1 || failures % 30 === 0) plog(`roster: ${r.status}: ${r.error}`, "warn");
        }
      }
    } finally {
      polling = false;
      const wait = failures ? Math.min(15000, every * (failures > 3 ? 3 : 1.5)) : every;
      timer = setTimeout(poll, document.hidden ? Math.max(wait, 10000) : wait);
      ageEl.textContent = lastOk ? `· ${fmtAge((Date.now() - lastOk) / 1000)}` : "";
    }
  }
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(true); });
  setInterval(() => { if (lastOk) ageEl.textContent = `· ${fmtAge((Date.now() - lastOk) / 1000)}`; }, 1000);
  poll(true);
  plog(`page opened (${title}, view ${view})`);
  // the server's own name (its server.jsonc), once the app has read it
  api.post(`/targets/${encodeURIComponent(state.here)}/check`).then((t) => {
    const opt = [...serverSel.options].find((o) => o.value === state.here);
    if (t && t.display && opt) { opt.textContent = `${t.display} — ${t.label}`; document.title = `${t.display} · Open77 Warden`; }
  }).catch(() => { /* said by the roster's status */ });

  // ---------------------------------------------------------------- the log
  async function openLog() {
    const pre = h("pre.w-log");
    const refresh = async () => {
      try {
        const r = await api.get("/log?since=0");
        pre.textContent = r.lines.map((e) => `${new Date(e.t * 1000).toLocaleTimeString()} ${e.level.padEnd(5)} ${e.src}: ${e.text}`).join("\n");
        pre.scrollTop = pre.scrollHeight;
      } catch (e) { pre.textContent = e.message; }
    };
    await refresh();
    const t = setInterval(refresh, 2000);
    await modal("The app's log", h("div", pre, h("p.w-note", `Kept in ${state.workspace}\\logs`)), [{ label: "Close", value: true }]);
    clearInterval(t);
  }
}

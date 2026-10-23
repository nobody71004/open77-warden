// The server's Warden from the app's page: this page shares the panel's origin (the app's address
// for this server), so a call to /api/... is a call to Warden itself, with the operator's own
// sign-in -- the panel's cookie, and whatever header the panel adds to its own calls (capture.js
// notes them in the panel's frame).  Warden checks every call against the operator's role and
// writes the actions in its audit, as if they were made in the panel.

let frameEl = null;
export function setPanelFrame(el) { frameEl = el; }

const TOKEN = /csrf|xsrf|antiforgery|anti-forgery|requestverification|forgery/i;
// the anti-forgery header names most panels use, beside the ones read in the panel's own scripts
const STD_NAMES = ["X-CSRF-Token", "X-XSRF-TOKEN", "RequestVerificationToken"];

// what capture.js saw in the panel's frame: { headers, shapes, tokens, names, calls }
export function learned() {
  try {
    const w = frameEl && frameEl.contentWindow;
    const s = w && w.__o77w;
    if (s) {
      return { headers: Object.assign({}, s.headers || {}), shapes: Object.assign({}, s.shapes || {}),
        tokens: Object.assign({}, s.tokens || {}), names: (s.names || []).slice(), calls: s.calls || 0 };
    }
  } catch (_) { /* the frame is elsewhere (an error page): nothing learned */ }
  return { headers: {}, shapes: {}, tokens: {}, names: [], calls: 0 };
}

// the newest anti-forgery token the panel can read ({ key, value } or null)
function newestToken(tokens) {
  let best = null;
  for (const [key, t] of Object.entries(tokens || {})) {
    const v = t && typeof t === "object" ? t.value : t;
    const at = t && typeof t === "object" ? t.at || 0 : 0;
    if (typeof v === "string" && v && (!best || at > best.at)) best = { key, value: v, at };
  }
  return best;
}

// the headers a change (POST...) carries beside the learned ones: when the panel was not seen
// sending an anti-forgery header yet (some panels add it to their changes only), the newest token
// it can read, under each name its scripts use and the usual ones
export function forgeryHeaders(l) {
  if (Object.keys(l.headers).some((k) => TOKEN.test(k))) return {};
  const t = newestToken(l.tokens);
  if (!t) return {};
  const out = {}, have = new Set();
  for (const n of l.names.concat(STD_NAMES)) {
    if (!have.has(n.toLowerCase())) { have.add(n.toLowerCase()); out[n] = t.value; }
  }
  return out;
}

export class WardenError extends Error {
  constructor(status, message, body) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

export async function call(method, path, body) {
  const l = learned();
  const change = method !== "GET" && method !== "HEAD";
  const forged = change ? forgeryHeaders(l) : {};
  const headers = Object.assign({ Accept: "application/json" }, l.headers, forged);
  const init = { method, credentials: "same-origin", headers, cache: "no-store" };
  if (body !== undefined) {
    init.body = JSON.stringify(body);
    headers["Content-Type"] = "application/json";
  }
  let r;
  try {
    r = await fetch(path, init);
  } catch (e) {
    throw new WardenError(0, `the app's own server did not answer (${e.message})`);
  }
  const ct = r.headers.get("Content-Type") || "";
  let json = null, text = "";
  try {
    if (ct.includes("json")) json = await r.json(); else text = await r.text();
  } catch (_) { /* an empty answer */ }
  // (the names of the headers sent, for the log: never their values)
  return { status: r.status, ok: r.ok && !(json && json.ok === false), json, text, ct,
    headers: Object.keys(l.headers), forged: Object.keys(forged) };
}

function why(res) {
  const j = res.json;
  if (j) {
    if (typeof j.error === "string") return j.error;
    if (j.error && j.error.message) return j.error.message;
    if (typeof j.message === "string") return j.message;
    if (typeof j.reason === "string") return j.reason;
    if (j.errors) return JSON.stringify(j.errors).slice(0, 300);
  }
  if (res.status === 401) return "not signed in to Warden";
  if (res.status === 403) return "your Warden role may not do this";
  return (res.text || `HTTP ${res.status}`).replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim().slice(0, 300);
}

// the roster: { ok, status, body, error, signedOut, unreachable }
export async function roster() {
  let res;
  try {
    res = await call("GET", "/api/players");
  } catch (e) {
    return { ok: false, status: 0, error: e.message, unreachable: true };
  }
  if (res.status === 401 || res.status === 403) {
    return { ok: false, status: res.status, signedOut: res.status === 401, forbidden: res.status === 403, error: why(res) };
  }
  if (res.status === 502 && res.json && res.json.o77w) return { ok: false, status: 502, unreachable: true, error: res.json.error };
  if (!res.ok || !res.json) {
    // a sign-in page instead of JSON is a signed-out panel too
    if (/<form|password/i.test(res.text || "")) return { ok: false, status: res.status, signedOut: true, error: "not signed in to Warden" };
    return { ok: false, status: res.status, error: why(res) };
  }
  return { ok: true, status: res.status, body: res.json };
}

// ------------------------------------------------------------------ the actions (Warden's Players tab)
// fields the panel was seen sending for an action are what is sent; else the names Warden's API
// documents (kick { reason }), and for a move every common name of its fields (unknown ones are
// ignored by the server)
function shaped(action, values, fallback) {
  const s = learned().shapes[action];
  if (!s || !s.keys || !s.keys.length) return fallback;
  const out = {};
  for (const k of s.keys) {
    const lk = k.toLowerCase();
    if (lk in values) out[k] = values[lk];
    else if (/reason|message|text/.test(lk) && values.text != null) out[k] = values.text;
    else if (/target|player|to/.test(lk) && values.target != null) out[k] = values.target;
    else if (/vehicle/.test(lk) && values.leavevehicle != null) out[k] = values.leavevehicle;
    else if (/^(position|pos|to|coords|destination)$/.test(lk) && values.x != null) out[k] = { x: values.x, y: values.y, z: values.z };
    else if (lk === "x" || lk === "y" || lk === "z") out[k] = values[lk];
  }
  return Object.keys(out).length ? out : fallback;
}

export const ACTIONS = {
  heal: { label: "Heal", path: "heal", perm: "players.heal" },
  revive: { label: "Revive", path: "heal", perm: "players.heal" },
  freeze: { label: "Freeze", path: "freeze", perm: "players.freeze" },
  unfreeze: { label: "Thaw", path: "unfreeze", perm: "players.freeze" },
  teleport: { label: "Move", path: "teleport", perm: "players.teleport" },
  warn: { label: "Warn", path: "warn", perm: "players.warn" },
  kick: { label: "Kick", path: "kick", perm: "players.kick" },
};

export function bodyFor(action, v) {
  switch (action) {
    case "kick": return shaped("kick", { reason: v.text, text: v.text }, { reason: v.text });
    case "warn": return shaped("warn", { message: v.text, reason: v.text, text: v.text }, { message: v.text, reason: v.text });
    case "teleport":
      if (v.target != null) {
        return shaped("teleport", { target: v.target, targetplayerid: v.target, toplayerid: v.target, leavevehicle: !!v.leaveVehicle },
          { targetPlayerId: v.target, toPlayerId: v.target, targetId: v.target, leaveVehicle: !!v.leaveVehicle });
      }
      return shaped("teleport", { x: v.x, y: v.y, z: v.z, leavevehicle: !!v.leaveVehicle },
        { x: v.x, y: v.y, z: v.z, position: { x: v.x, y: v.y, z: v.z }, leaveVehicle: !!v.leaveVehicle });
    default: return {};
  }
}

// Warden's named outcomes (the Players tab's own words for them)
const OUTCOMES = {
  arrived: "arrived (standing on solid ground)",
  pending: "pending: their game has not answered yet (the move may still land)",
  player_in_vehicle: "they are in a vehicle (tick \"Take them out of the vehicle first\")",
  player_not_alive: "they are dead: revive them first",
  settle_timeout: "their game did not see them settle on solid ground in time",
  not_ready: "they are still joining (behind the readiness gate)",
  player_not_ready: "they are still joining (behind the readiness gate)",
  still_held: "released by the panel, but a script still holds them frozen",
};
const say = (s) => (s && OUTCOMES[s]) || s;

// a refusal that names the anti-forgery check (or the origin check) rather than the action
function forgeryRefusal(res, text) {
  return [400, 403, 419, 422].includes(res.status) && /forg|csrf|xsrf|cross.?(site|origin)|origin|verification/i.test(text || "");
}

// one action on one player: { ok, status, message, outcome, body, sent, forgery }
export async function act(playerId, action, values = {}) {
  const a = ACTIONS[action];
  if (!a) throw new Error(`no such action ${action}`);
  const body = bodyFor(action, values);
  const res = await call("POST", `/api/players/${encodeURIComponent(playerId)}/${a.path}`, body);
  const j = res.json || {};
  const outcome = typeof j.outcome === "string" ? j.outcome : typeof j.result === "string" ? j.result : typeof j.state === "string" ? j.state : null;
  const text = res.ok ? (typeof j.message === "string" && j.message) || say(outcome) || "done" : why(res);
  const forgery = !res.ok && forgeryRefusal(res, text);
  return { ok: res.ok, status: res.status, message: res.ok ? text : say(text), outcome, body: j, sent: Object.keys(body),
    headers: res.headers, forged: res.forged, forgery };
}

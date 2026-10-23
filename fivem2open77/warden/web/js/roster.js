// Warden's roster (GET /api/players) as the live map's players.  The answer is read tolerantly: the
// list may be the answer itself or under players / data / items / roster; a player's fields under
// the names the platform uses (Open77.players.get: playerId, position {x, y, z}, ageMs, heading / yaw,
// bucket, stateFlags) or the ones of Warden's Players tab (ID, Name, Identity, Ping, Health and
// armour, State, Match, Position and its age).  What it could not read is said once in the log.

const NUM = (v) => (typeof v === "number" ? v : typeof v === "string" && v.trim() !== "" && !Number.isNaN(Number(v)) ? Number(v) : null);
const first = (...vs) => { for (const v of vs) if (v !== undefined && v !== null && v !== "") return v; return null; };

export function rosterList(body) {
  if (Array.isArray(body)) return body;
  if (!body || typeof body !== "object") return [];
  for (const k of ["players", "data", "items", "roster", "result", "list"]) {
    const v = body[k];
    if (Array.isArray(v)) return v;
    if (v && typeof v === "object") { const inner = rosterList(v); if (inner.length) return inner; }
  }
  return [];
}

function vec(v) {
  if (!v) return null;
  if (Array.isArray(v) && v.length >= 2) return [NUM(v[0]), NUM(v[1]), NUM(v[2]) ?? 0];
  if (typeof v === "object") {
    const x = NUM(first(v.x, v.X)), y = NUM(first(v.y, v.Y)), z = NUM(first(v.z, v.Z));
    if (x !== null && y !== null) return [x, y, z ?? 0];
  }
  if (typeof v === "string") {
    const m = v.match(/(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)(?:\s*[, ]\s*(-?\d+(?:\.\d+)?))?/);
    if (m) return [Number(m[1]), Number(m[2]), m[3] != null ? Number(m[3]) : 0];
  }
  return null;
}

function seconds(ms, s, at) {
  const a = NUM(ms);
  if (a !== null) return a / 1000;
  const b = NUM(s);
  if (b !== null) return b;
  if (at) {
    const t = typeof at === "number" ? (at > 1e12 ? at : at * 1000) : Date.parse(at);
    if (Number.isFinite(t)) return Math.max(0, (Date.now() - t) / 1000);
  }
  return null;
}

const STATE_NAMES = {
  dead: "dead", downed: "dead", loading: "loading", frozen: "frozen", god: "god", godmode: "god", ghost: "ghost",
  hidden: "hidden", invisible: "hidden", vehicle: "vehicle", invehicle: "vehicle", "in vehicle": "vehicle", in_vehicle: "vehicle",
  driving: "vehicle", watching: "watching", spectating: "watching", notready: "notready", "not ready": "notready", not_ready: "notready",
  alive: "alive",
};

function statesOf(r) {
  const out = new Set();
  const add = (s) => {
    if (s == null) return;
    const k = String(s).toLowerCase().replace(/\s*#\d+$/, "").trim();
    const n = STATE_NAMES[k] || STATE_NAMES[k.replace(/[^a-z]/g, "")];
    if (n && n !== "alive") out.add(n);
  };
  for (const src of [r.states, r.state, r.flags, r.stateFlags, r.status, r.chips]) {
    if (Array.isArray(src)) src.forEach(add);
    else if (src && typeof src === "object") for (const [k, v] of Object.entries(src)) { if (v === true) add(k); }
    else if (typeof src === "string") src.split(/[,|]/).forEach(add);
  }
  const yes = (v) => v === true || v === 1 || v === "true";
  if (yes(r.dead) || yes(r.isDead)) out.add("dead");
  if (yes(r.frozen) || yes(r.isFrozen)) out.add("frozen");
  if (yes(r.inVehicle) || yes(r.isInVehicle) || (r.vehicle && typeof r.vehicle === "object")) out.add("vehicle");
  if (yes(r.god) || yes(r.godMode)) out.add("god");
  if (yes(r.ghost) || yes(r.isGhost)) out.add("ghost");
  if (yes(r.hidden) || yes(r.isHidden)) out.add("hidden");
  if (yes(r.loading) || yes(r.isLoading)) out.add("loading");
  if (r.ready === false || r.isReady === false) out.add("notready");
  if (first(r.watching, r.spectating, r.watchingId) != null && r.watching !== false) out.add("watching");
  return [...out];
}

function health(r) {
  const hv = r.health;
  let cur = null, max = null, ar = null, arMax = null;
  if (hv && typeof hv === "object") {
    cur = NUM(first(hv.current, hv.value, hv.hp, hv.health, hv.points));
    max = NUM(first(hv.max, hv.maximum, hv.maxHealth));
    ar = NUM(first(hv.armor, hv.armour));
    arMax = NUM(first(hv.armorMax, hv.armourMax, hv.maxArmor));
    if (cur === null && NUM(hv.percent) !== null) { cur = NUM(hv.percent); max = 100; }
  } else {
    cur = NUM(first(hv, r.hp, r.healthPoints));
    max = NUM(first(r.healthMax, r.maxHealth, r.maxHp));
    if (cur === null && NUM(first(r.healthPercent, r.healthPct)) !== null) { cur = NUM(first(r.healthPercent, r.healthPct)); max = 100; }
  }
  if (ar === null) {
    const a = first(r.armor, r.armour);
    if (a && typeof a === "object") { ar = NUM(first(a.current, a.value)); arMax = NUM(first(a.max, a.maximum)); } else ar = NUM(a);
  }
  if (arMax === null) arMax = NUM(first(r.armorMax, r.armourMax, r.maxArmor));
  return { cur, max, ar, arMax };
}

export function normalizePlayer(r, i) {
  if (!r || typeof r !== "object") return null;
  const id = first(r.id, r.playerId, r.netId, r.sessionId, r.player && r.player.id);
  const identity = first(r.identity, r.userId, r.guid, r.accountId, r.identityId, r.account && r.account.id);
  const name = first(r.name, r.displayName, r.username, r.userName, r.nickname, r.player && r.player.name);
  const posSrc = first(r.position, r.pos, r.coords, r.coordinates, r.location, r.lastPosition, r.transform && r.transform.position);
  let p = vec(posSrc);
  if (!p && NUM(r.x) !== null && NUM(r.y) !== null) p = [NUM(r.x), NUM(r.y), NUM(r.z) ?? 0];
  const pos = posSrc && typeof posSrc === "object" && !Array.isArray(posSrc) ? posSrc : {};
  const age = seconds(first(r.positionAgeMs, r.ageMs, pos.ageMs, r.positionAge_ms), first(r.positionAge, r.age, pos.age, r.positionAgeSeconds),
    first(r.positionAt, r.positionTime, pos.at, pos.time, r.lastSeen, r.updatedAt));
  const yaw = NUM(first(r.yaw, r.heading, pos.yaw, pos.heading, r.rotation && r.rotation.yaw, r.orientation && r.orientation.yaw));
  const hp = health(r);
  const ping = NUM(first(r.ping, r.pingMs, r.rtt, r.rttMs, r.latency, r.latencyMs));
  let bucket = first(r.bucket, r.routingBucket, r.match, r.matchId, r.dimension);
  if (typeof bucket === "string") bucket = /^lobby$/i.test(bucket) ? 0 : bucket.replace(/^#/, "");
  const key = id != null ? `id:${id}` : identity != null ? `who:${identity}` : `row:${i}`;
  return {
    key, id, identity, name: name != null ? String(name) : null,
    x: p ? p[0] : null, y: p ? p[1] : null, z: p ? p[2] : null, yaw,
    health: hp.cur, healthMax: hp.max, armor: hp.ar, armorMax: hp.arMax,
    ping, states: statesOf(r), bucket, age,
    connectedAt: first(r.connectedAt, r.connectedAtUtc, r.joinedAt, r.connected), raw: r,
  };
}

// what the roster looks like, for the log (the keys, never the values)
export function shapeOf(body) {
  const list = rosterList(body);
  const top = Array.isArray(body) ? "[...]" : body && typeof body === "object" ? `{${Object.keys(body).join(", ")}}` : typeof body;
  const one = list[0] && typeof list[0] === "object" ? Object.keys(list[0]).join(", ") : "-";
  const pos = list[0] && typeof list[0] === "object" ? (() => { const v = first(list[0].position, list[0].pos, list[0].coords); return v && typeof v === "object" ? `{${Object.keys(v).join(", ")}}` : typeof v; })() : "-";
  return `answer ${top}; ${list.length} player(s); a player {${one}}; position ${pos}`;
}

export function normalize(body) {
  const list = rosterList(body);
  const players = [], unplaced = [];
  list.forEach((r, i) => {
    const p = normalizePlayer(r, i);
    if (!p) return;
    if (Number.isFinite(p.x) && Number.isFinite(p.y)) players.push(p); else unplaced.push(p);
  });
  return { players, unplaced, count: list.length };
}

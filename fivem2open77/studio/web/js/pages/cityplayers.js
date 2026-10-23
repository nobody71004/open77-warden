// The players, for the 3D city: a hologram where each one stands -- a figure of light turned the
// way they face, in their state's colour -- with a beam and a ring that keep them findable from high
// up, a fading trail of where they have been, and a label with their name, health and armour, ping
// and state.  Positions arrive every few seconds (Warden's roster, the map manager's list); between
// two a hologram glides from where it was to where it is now, so it moves as the player does, and
// a jump (a teleport) is a jump.  The figure grows with the distance so it stays a few dozen pixels
// tall from anywhere, and it shows through buildings, dimmed.
//
// playerLayer(ctx) -> { set(players, now), frame(now, camera), labels(place, maxDist), hit(x, y, project),
//                       pos(key), get(key), list(), select(key), moving(), dispose() }
//   ctx: { THREE, S (cityshaders), group (Night City's frame), labelLayer, beamMesh, ringMesh, onClick(p) }
//   players: [{ key, name, x, y, z, yaw? (degrees, 0 north, counter-clockwise), health?, healthMax?, armor?,
//               armorMax?, ping?, states?: ["dead" | "frozen" | "vehicle" | "god" | "ghost" | "hidden" |
//               "loading" | "notready" | "watching"], bucket?, age? (seconds the position is old), id?, sub? }]
//   Only key, name and x, y, z are needed (the Studio's Live maps give just those).
import { mergeGeometries } from "../../vendor/three/BufferGeometryUtils.js";
import { h } from "../ui.js";

const COLORS = { alive: 0x47d4ff, dead: 0xff3b4e, frozen: 0xbfeaff, vehicle: 0xffd23f, god: 0xc49bff, ghost: 0x8b95ab,
  hidden: 0x8b95ab, loading: 0x8b95ab, notready: 0x8b95ab, watching: 0xb38cff };
const ORDER = ["dead", "frozen", "vehicle", "god", "watching", "ghost", "hidden", "loading", "notready"];
const TRAIL_MAX = 160;                 // points kept per player
const HEIGHT = 1.8;                    // the figure, metres
const SNAP = 250;                      // a move longer than this between two updates is a jump

function stateOf(p) {
  const s = new Set(p.states || []);
  for (const k of ORDER) if (s.has(k)) return k;
  return "alive";
}

// the figure, in its own frame: feet at 0, facing +y (north), z up
function figureGeometry(THREE) {
  const parts = [];
  const add = (g, x, y, z, rx = 0, ry = 0, rz = 0) => {
    g.rotateX(rx); g.rotateY(ry); g.rotateZ(rz);
    g.translate(x, y, z);
    parts.push(g.index ? g : g);
  };
  const cyl = (rt, rb, hgt, seg = 10) => { const g = new THREE.CylinderGeometry(rt, rb, hgt, seg, 1, false); g.rotateX(Math.PI / 2); return g; };
  add(new THREE.SphereGeometry(0.115, 14, 10), 0, 0.01, 1.66);                      // head
  add(cyl(0.045, 0.05, 0.1), 0, 0, 1.52);                                              // neck
  add(cyl(0.2, 0.15, 0.5, 12), 0, 0, 1.22);                                           // chest
  add(cyl(0.15, 0.16, 0.16, 12), 0, 0, 0.92);                                         // hips
  for (const sx of [-1, 1]) {
    add(cyl(0.075, 0.055, 0.86), sx * 0.09, 0, 0.43);                                  // legs
    add(cyl(0.05, 0.04, 0.6), sx * 0.255, 0, 1.17, 0, sx * 0.14, 0);                   // arms
  }
  // a pointer on the ground, ahead: where they face
  const cone = new THREE.ConeGeometry(0.16, 0.42, 3);
  cone.rotateX(-Math.PI / 2);
  cone.rotateZ(Math.PI);
  cone.translate(0, 0.62, 0.04);
  parts.push(cone);
  return mergeGeometries(parts.map((g) => g.toNonIndexed()), false);
}

function holoShaders(S) {
  const vs = S.VHEAD + /* glsl */ `
varying vec3 vN; varying vec3 vV; varying float vH;
void main() {
  vH = position.z;
  vec4 wp = modelMatrix * vec4(position, 1.0);
  vN = normalize(mat3(modelMatrix) * normal);
  vV = normalize(cameraPosition - wp.xyz);
  gl_Position = projectionMatrix * viewMatrix * wp;
  #include <logdepthbuf_vertex>
}`;
  const fs = S.FHEAD + /* glsl */ `
uniform float uTime; uniform vec3 uColor; uniform float uAlpha; uniform float uSeed; uniform float uSel;
varying vec3 vN; varying vec3 vV; varying float vH;
void main() {
  #include <logdepthbuf_fragment>
  float rim = pow(1.0 - abs(dot(safeNormalize(vN), safeNormalize(vV))), 1.7);
  float h = vH / ${HEIGHT.toFixed(2)};
  float scan = 0.62 + 0.38 * sin(h * 120.0 - uTime * 7.0);
  float band = exp(-sq((fract(h * 0.8 - uTime * 0.45) - 0.5) * 9.0));
  float flick = 0.86 + 0.14 * sin(uTime * 31.0 + uSeed * 17.0) * sin(uTime * 7.3 + uSeed);
  float glitch = step(0.985, fract(sin(floor(uTime * 9.0) + uSeed * 91.7) * 4375.85)) * 0.6;
  vec3 col = uColor * (0.42 + rim * 1.7 + uSel * 0.45) * scan * flick * uAlpha;
  col += uColor * band * 0.55 * uAlpha + vec3(glitch) * uColor * uAlpha;
  gl_FragColor = vec4(col, 1.0);
}`;
  const trailVS = S.VHEAD + /* glsl */ `
attribute vec2 aDir; attribute float aSide; attribute float aT;
uniform float uPxScale; uniform float uPx;
varying float vSide; varying float vT;
void main() {
  vec4 w0 = modelMatrix * vec4(position, 1.0);
  float d = distance(w0.xyz, cameraPosition);
  float w = max(0.35, d * uPxScale * uPx);
  vec2 perp = vec2(-aDir.y, aDir.x) * inversesqrt(max(dot(aDir, aDir), 1e-12));
  vec3 p = position + vec3(perp * aSide * w * 0.5, 0.0);
  vSide = aSide; vT = aT;
  gl_Position = projectionMatrix * viewMatrix * modelMatrix * vec4(p, 1.0);
  #include <logdepthbuf_vertex>
}`;
  const trailFS = S.FHEAD + /* glsl */ `
uniform vec3 uColor; uniform float uNow; uniform float uSpan; uniform float uAlpha;
varying float vSide; varying float vT;
void main() {
  #include <logdepthbuf_fragment>
  float age = clamp((uNow - vT) / max(uSpan, 0.001), 0.0, 1.0);
  float a = pow(1.0 - age, 1.4) * uAlpha;
  float prof = exp(-vSide * vSide * 2.4);
  gl_FragColor = vec4(uColor * prof * a * 0.9, 1.0);
}`;
  return { vs, fs, trailVS, trailFS };
}

export function playerLayer(ctx) {
  const { THREE, S, group, labelLayer } = ctx;
  const sh = holoShaders(S);
  const geo = figureGeometry(THREE);
  const U = ctx.uniforms;               // uTime, uPxScale (the viewer's)
  const additive = { transparent: true, depthWrite: false, blending: THREE.AdditiveBlending };
  const marks = new Map();              // key -> mark
  let selected = null;
  let trailSpan = 30;

  function holoMat(color, seed, depth) {
    return new THREE.ShaderMaterial(Object.assign({
      vertexShader: sh.vs, fragmentShader: sh.fs,
      uniforms: { uTime: U.uTime, uColor: { value: new THREE.Color(color) }, uAlpha: { value: depth ? 1 : 0.38 },
        uSeed: { value: seed }, uSel: { value: 0 } },
      side: THREE.DoubleSide, depthTest: depth,
    }, additive));
  }

  function trailOf(color) {
    const n = TRAIL_MAX;
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(n * 4 * 3), 3));
    g.setAttribute("aDir", new THREE.BufferAttribute(new Float32Array(n * 4 * 2), 2));
    g.setAttribute("aSide", new THREE.BufferAttribute(new Float32Array(n * 4), 1));
    g.setAttribute("aT", new THREE.BufferAttribute(new Float32Array(n * 4), 1));
    const idx = new Uint32Array((n - 1) * 6);
    for (let i = 0; i < n - 1; i++) {
      const b = i * 4;
      idx.set([b, b + 1, b + 2, b + 1, b + 3, b + 2], i * 6);
    }
    g.setIndex(new THREE.BufferAttribute(idx, 1));
    g.setDrawRange(0, 0);
    const m = new THREE.Mesh(g, new THREE.ShaderMaterial(Object.assign({
      vertexShader: sh.trailVS, fragmentShader: sh.trailFS,
      uniforms: { uPxScale: U.uPxScale, uPx: { value: 2.6 }, uColor: { value: new THREE.Color(color) }, uNow: { value: 0 },
        uSpan: { value: trailSpan }, uAlpha: { value: 0.85 } },
      depthTest: false, side: THREE.DoubleSide,           // (a flat ribbon: seen from above or below)
    }, additive)));
    m.frustumCulled = false;
    m.renderOrder = 10;
    return m;
  }

  // the trail's segments from the samples (and the hologram where it is now, last)
  function writeTrail(mk, head, nowS) {
    const pts = mk.hist.slice(-(TRAIL_MAX - 1));
    if (head) pts.push(head);
    const g = mk.trail.geometry;
    const P = g.attributes.position.array, D = g.attributes.aDir.array, Sd = g.attributes.aSide.array, T = g.attributes.aT.array;
    const segs = Math.max(0, pts.length - 1);
    for (let i = 0; i < segs; i++) {
      const a = pts[i], b = pts[i + 1];
      const dx = b[0] - a[0], dy = b[1] - a[1], len = Math.hypot(dx, dy) || 1;
      let v = i * 4;
      for (const [q, side] of [[a, -1], [a, 1], [b, -1], [b, 1]]) {
        P[v * 3] = q[0]; P[v * 3 + 1] = q[1]; P[v * 3 + 2] = q[2] + 0.35;
        D[v * 2] = dx / len; D[v * 2 + 1] = dy / len;
        Sd[v] = side; T[v] = q[3];
        v++;
      }
    }
    g.setDrawRange(0, segs * 6);
    for (const k of ["position", "aDir", "aSide", "aT"]) g.attributes[k].needsUpdate = true;
    mk.trail.material.uniforms.uNow.value = nowS;
  }

  function labelFor(p) {
    const name = h("b");
    const meta = h("span.hl-meta");
    const hp = h("i.hl-hp"), ar = h("i.hl-ar");
    const bar = h("i.hl-bar", hp, ar);
    const chips = h("span.hl-chips");
    const tag = h("div.c3-tag.click.hl-tag", name, meta, bar, chips);
    const e = h("div.c3-label.player.holo", tag);
    tag.addEventListener("click", (ev) => { ev.stopPropagation(); const mk = marks.get(p.key); if (mk && ctx.onClick) ctx.onClick(mk.p, ev); });
    labelLayer.append(e);
    return { e, name, meta, hp, ar, bar, chips };
  }

  function fillLabel(mk) {
    const p = mk.p, L = mk.label;
    L.name.textContent = p.name || `player ${p.id != null ? p.id : ""}`;
    const meta = [];
    if (p.id != null) meta.push(`#${p.id}`);
    if (Number.isFinite(p.ping)) meta.push(`${Math.round(p.ping)} ms`);
    if (p.bucket != null && p.bucket !== 0 && p.bucket !== "0") meta.push(`match ${p.bucket}`);
    if (Number.isFinite(p.age) && p.age > 2) meta.push(`${p.age < 90 ? Math.round(p.age) + " s" : Math.round(p.age / 60) + " min"} ago`);
    if (p.sub) meta.push(p.sub);
    L.meta.textContent = meta.join(" · ") || "PLAYER";
    const hasHp = Number.isFinite(p.health);
    L.bar.style.display = hasHp ? "" : "none";
    if (hasHp) {
      const max = Number.isFinite(p.healthMax) && p.healthMax > 0 ? p.healthMax : 100;
      L.hp.style.width = `${Math.max(0, Math.min(100, p.health / max * 100)).toFixed(0)}%`;
      const amax = Number.isFinite(p.armorMax) && p.armorMax > 0 ? p.armorMax : 100;
      L.ar.style.width = Number.isFinite(p.armor) ? `${Math.max(0, Math.min(100, p.armor / amax * 100)).toFixed(0)}%` : "0%";
    }
    L.chips.textContent = "";
    for (const s of p.states || []) if (s !== "alive") L.chips.append(h("u", s === "vehicle" ? "in vehicle" : s === "notready" ? "not ready" : s));
    L.e.dataset.state = mk.state;
    mk.size = null;                      // (measured again: the label's text changed)
    L.e.classList.toggle("sel", selected === p.key);
    L.e.classList.toggle("stale", Number.isFinite(p.age) && p.age > 10);
  }

  function create(p) {
    const seed = Math.random();
    const state = stateOf(p);
    const color = COLORS[state];
    const root = new THREE.Group();
    const fig = new THREE.Group();
    const body = new THREE.Mesh(geo, holoMat(color, seed, true));
    const xray = new THREE.Mesh(geo, holoMat(color, seed, false));
    body.renderOrder = 14; xray.renderOrder = 15;
    body.frustumCulled = xray.frustumCulled = false;
    fig.add(body, xray);
    const ring = ctx.ringMesh(color, 1.4, seed);
    ring.position.z = 0.05;
    fig.add(ring);
    const beam = ctx.beamMesh(color, 140, 0.7, 1.0);
    root.add(fig, beam);
    group.add(root);
    const trail = trailOf(color);
    group.add(trail);
    const mk = { root, fig, body, xray, ring, beam, trail, state, color, seed, hist: [], from: null, to: null, t0: 0, dur: 2,
      lastAt: 0, cur: [p.x, p.y, p.z || 0], yaw: 0, scale: 1, label: labelFor(p), p };
    marks.set(p.key, mk);
    return mk;
  }

  function recolor(mk) {
    const c = COLORS[mk.state];
    for (const m of [mk.body, mk.xray]) m.material.uniforms.uColor.value.setHex(c);
    mk.ring.material.uniforms.uColor.value.setHex(c);
    mk.beam.material.uniforms.uColor.value.setHex(c);
    mk.trail.material.uniforms.uColor.value.setHex(c);
  }

  function dispose1(mk) {
    group.remove(mk.root);
    group.remove(mk.trail);
    mk.root.traverse((o) => { if (o.material) o.material.dispose(); if (o.geometry && o.geometry !== geo) o.geometry.dispose(); });
    mk.trail.geometry.dispose();
    mk.trail.material.dispose();
    mk.label.e.remove();
  }

  // ------------------------------------------------------------------ the API
  function set(players, now = performance.now(), opts = {}) {
    if (Number.isFinite(opts.trailSeconds)) trailSpan = Math.max(0, opts.trailSeconds);
    const nowS = now / 1000;
    const seen = new Set();
    for (const p of players || []) {
      if (!p || !Number.isFinite(p.x) || !Number.isFinite(p.y)) continue;
      seen.add(p.key);
      let mk = marks.get(p.key);
      const z = Number.isFinite(p.z) ? p.z : 0;
      if (!mk) {
        mk = create(p);
        mk.from = [p.x, p.y, z];
        mk.to = [p.x, p.y, z];
        mk.cur = [p.x, p.y, z];
        mk.hist.push([p.x, p.y, z, nowS]);
      } else {
        const moved = Math.hypot(p.x - mk.to[0], p.y - mk.to[1], z - mk.to[2]);
        // the time between two updates is how long the glide takes (the poll's own rhythm)
        const gap = mk.lastAt ? (now - mk.lastAt) / 1000 : 2;
        mk.dur = Math.max(0.25, Math.min(6, gap * 1.05));
        if (moved > SNAP) {                                       // a jump: no glide, and the trail starts again
          mk.from = [p.x, p.y, z]; mk.cur = [p.x, p.y, z]; mk.hist = [];
        } else {
          mk.from = mk.cur.slice();
        }
        mk.to = [p.x, p.y, z];
        mk.t0 = now;
        if (moved > 0.4) {
          const d = [p.x - mk.from[0], p.y - mk.from[1]];
          if (Math.hypot(d[0], d[1]) > 0.4) mk.motionYaw = Math.atan2(-d[0], d[1]);   // (0 north, counter-clockwise)
        }
        if (moved > 0.25 || !mk.hist.length) mk.hist.push([p.x, p.y, z, nowS]);
      }
      mk.lastAt = now;
      // the trail: what is not older than its span
      while (mk.hist.length && nowS - mk.hist[0][3] > trailSpan) mk.hist.shift();
      if (mk.hist.length > TRAIL_MAX) mk.hist.splice(0, mk.hist.length - TRAIL_MAX);
      const st = stateOf(p);
      mk.p = p;
      if (st !== mk.state) { mk.state = st; recolor(mk); }
      mk.headingYaw = Number.isFinite(p.yaw) ? p.yaw * Math.PI / 180 : null;
      const ghosty = st === "ghost" || st === "hidden" || st === "loading" || st === "notready";
      mk.body.material.uniforms.uAlpha.value = ghosty ? 0.45 : st === "dead" ? 0.6 : 1;
      mk.xray.material.uniforms.uAlpha.value = ghosty ? 0.14 : 0.38;
      fillLabel(mk);
    }
    for (const [k, mk] of marks) if (!seen.has(k)) { dispose1(mk); marks.delete(k); if (selected === k) selected = null; }
  }

  const tmp = new THREE.Vector3();
  function frame(now, camera) {
    const nowS = now / 1000;
    for (const mk of marks.values()) {
      const t = mk.t0 ? Math.min(1, (now - mk.t0) / (mk.dur * 1000)) : 1;
      const k = t < 1 ? t * t * (3 - 2 * t) * 0.35 + t * 0.65 : 1;           // (near linear: steady walking)
      for (let i = 0; i < 3; i++) mk.cur[i] = mk.from[i] + (mk.to[i] - mk.from[i]) * k;
      const [x, y, z] = mk.cur;
      mk.root.position.set(x, y, z);
      // facing: where they head (the roster's heading, else the way they move)
      const want = mk.headingYaw != null ? mk.headingYaw : mk.motionYaw != null ? mk.motionYaw : mk.yaw;
      const dy = Math.atan2(Math.sin(want - mk.yaw), Math.cos(want - mk.yaw));
      mk.yaw += dy * 0.18;
      mk.fig.rotation.z = mk.yaw;
      // as tall as a person up close, a few dozen pixels from afar
      tmp.set(x, z, -y);
      const d = camera.position.distanceTo(tmp);
      mk.dist = d;
      const s = Math.max(1, Math.min(120, d / 32));
      mk.scale = s;
      mk.fig.scale.setScalar(s);
      const dead = mk.state === "dead";
      mk.fig.rotation.x = dead ? -Math.PI / 2 * 0.92 : 0;                       // the fallen lie down
      mk.fig.position.z = dead ? 0.25 * s : 0;
      mk.top = [x, y, z + (dead ? 0.8 : HEIGHT + 0.55) * s];
      if (trailSpan > 0 && mk.hist.length) writeTrail(mk, [x, y, z, nowS], nowS);
      else mk.trail.geometry.setDrawRange(0, 0);
      mk.trail.material.uniforms.uSpan.value = trailSpan || 1;
    }
  }

  // the labels where they are, nearer players first; one that would cover another goes up above it
  // (its stem grows to its player), so a crowd stays readable
  function labels(place, maxDist) {
    const items = [];
    for (const mk of marks.values()) {
      if (!mk.top || !place(mk.label.e, mk.top, maxDist)) continue;
      const s = ctx.toScreen ? ctx.toScreen(mk.top) : null;
      if (!s) continue;
      if (!mk.size) { const t = mk.label.e.firstChild; mk.size = [t.offsetWidth || 120, t.offsetHeight || 32]; }
      items.push({ mk, x: s[0], y: s[1], w: mk.size[0], h: mk.size[1], d: mk.dist || 0 });
    }
    items.sort((a, b) => a.d - b.d);
    const placed = [];
    for (const it of items) {
      let lift = 0;
      for (let guard = 0; guard < 12; guard++) {
        const r = { l: it.x - it.w / 2 - 2, r: it.x + it.w / 2 + 2, b: it.y - 8 - lift, t: it.y - 8 - lift - it.h };
        const hit = placed.find((p) => r.l < p.r && r.r > p.l && r.t < p.b && r.b > p.t);
        if (!hit) { placed.push(r); break; }
        lift += r.b - hit.t + 3;
      }
      if (it.mk.lift !== lift) { it.mk.lift = lift; it.mk.label.e.style.setProperty("--lift", `${lift}px`); }
    }
  }

  // the player whose hologram is under the screen point (x, y), if any: project(p) -> [sx, sy] | null
  function hit(x, y, project) {
    let best = null, bd = 24;
    for (const mk of marks.values()) {
      const a = project([mk.cur[0], mk.cur[1], mk.cur[2] + HEIGHT * 0.5 * mk.scale]);
      if (!a) continue;
      const d = Math.hypot(a[0] - x, a[1] - y);
      if (d < bd) { bd = d; best = mk; }
    }
    return best ? best.p : null;
  }

  function select(key) {
    selected = key || null;
    for (const [k, mk] of marks) {
      const on = k === selected ? 1 : 0;
      mk.body.material.uniforms.uSel.value = on;
      mk.label.e.classList.toggle("sel", !!on);
      mk.ring.scale.setScalar(on ? 2.2 : 1.4);
    }
  }

  return {
    set, frame, labels, hit, select,
    pos: (key) => { const mk = marks.get(key); return mk ? mk.cur.slice() : null; },
    get: (key) => { const mk = marks.get(key); return mk ? mk.p : null; },
    list: () => [...marks.values()].map((mk) => mk.p),
    moving: () => { const now = performance.now(); for (const mk of marks.values()) if (mk.t0 && now - mk.t0 < mk.dur * 1000) return true; return false; },
    // (tests: each trail's samples and what it draws)
    trails: () => [...marks.entries()].map(([k, mk]) => ({ key: k, samples: mk.hist.length, drawn: mk.trail.geometry.drawRange.count,
      span: trailSpan, first: mk.hist[0] || null, last: mk.hist[mk.hist.length - 1] || null, visible: mk.trail.visible && mk.trail.parent === group,
      bothSides: mk.trail.material.side === THREE.DoubleSide })),
    dispose() { for (const mk of marks.values()) dispose1(mk); marks.clear(); geo.dispose(); },
  };
}

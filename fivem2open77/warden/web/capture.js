// Open77 Warden app: put into the server's Warden pages by the app (the panel's own origin, before
// the panel's own scripts).  It notes how the panel calls its own API, so the app's live map (same
// origin, framing this page) calls the API the same way:
//   headers   the headers the panel adds to its API calls (a sign-in token, an anti-forgery header)
//   shapes    the names of the fields the panel sends with each player action (/api/players/<id>/<action>)
//   tokens    anti-forgery tokens the panel can read (a cookie, a <meta>, a field of an API answer),
//             each { value, at } (at: when that value was first seen, so the newest is the live one)
//   names     the anti-forgery header names written in the panel's own scripts
// It keeps them in this page only (window.__o77w) and sends nothing anywhere.  It also lets a page that
// does not scroll itself scroll when it is taller than the app's frame (unclip, below).
(function () {
  "use strict";
  if (window.__o77w) return;
  var STD = /^(accept|accept-language|content-type|content-length|cache-control|pragma)$/i;
  var TOKEN = /csrf|xsrf|antiforgery|anti-forgery|requestverification|forgery/i;
  var seen = { headers: {}, shapes: {}, tokens: {}, names: [], calls: 0, at: 0 };
  try { Object.defineProperty(window, "__o77w", { value: seen, enumerable: false }); } catch (_) { window.__o77w = seen; }

  function apiPath(url) {
    try {
      var u = new URL(url, location.href);
      return u.origin === location.origin && u.pathname.indexOf("/api/") === 0 ? u.pathname : null;
    } catch (_) { return null; }
  }
  function token(key, value) {
    var t = seen.tokens[key];
    if (!t || t.value !== value) seen.tokens[key] = { value: value, at: Date.now() };
  }
  function keysOf(body) {
    if (typeof body !== "string") return null;
    try { var v = JSON.parse(body); return v && typeof v === "object" && !Array.isArray(v) ? Object.keys(v) : null; } catch (_) { return null; }
  }
  function note(method, path, headers, body) {
    seen.calls++;
    seen.at = Date.now();
    for (var k in headers) {
      if (Object.prototype.hasOwnProperty.call(headers, k) && !STD.test(k)) seen.headers[k] = headers[k];
    }
    var m = /^\/api\/players\/[^/]+\/([a-z_-]+)$/i.exec(path);
    if (m && method !== "GET") {
      var keys = keysOf(body);
      if (keys) seen.shapes[m[1].toLowerCase()] = { keys: keys, at: Date.now() };
    }
  }
  function plain(h) {
    var out = {};
    if (!h) return out;
    try {
      if (typeof Headers !== "undefined" && h instanceof Headers) { h.forEach(function (v, k) { out[k] = v; }); return out; }
      if (Array.isArray(h)) { h.forEach(function (p) { out[String(p[0])] = String(p[1]); }); return out; }
      for (var k in h) if (Object.prototype.hasOwnProperty.call(h, k)) out[k] = String(h[k]);
    } catch (_) { /* odd headers: none */ }
    return out;
  }
  // a token in an API answer: { csrf: "..." }, { session: { xsrfToken: "..." } }
  function scanAnswer(v, depth) {
    if (!v || typeof v !== "object" || depth > 2) return;
    for (var k in v) {
      if (!Object.prototype.hasOwnProperty.call(v, k)) continue;
      var x = v[k];
      if (typeof x === "string" && TOKEN.test(k) && x.length >= 8 && x.length <= 4096) token("answer:" + k, x);
      else if (x && typeof x === "object" && !Array.isArray(x)) scanAnswer(x, depth + 1);
    }
  }
  function scanPage() {
    try {
      document.cookie.split(";").forEach(function (c) {
        var i = c.indexOf("=");
        var k = c.slice(0, i).trim(), v = c.slice(i + 1).trim();
        if (k && TOKEN.test(k) && v) {
          var dv = v;
          try { dv = decodeURIComponent(v); } catch (_) { /* as it is */ }
          token("cookie:" + k, dv);
        }
      });
      var metas = document.querySelectorAll("meta[name]");
      for (var i = 0; i < metas.length; i++) {
        var n = metas[i].getAttribute("name");
        if (TOKEN.test(n) && metas[i].content) token("meta:" + n, metas[i].content);
      }
    } catch (_) { /* */ }
  }
  // the header names the panel's own scripts write ("X-Foo-Csrf"...), from their source: the
  // page's scripts, inline and loaded, and the chunks they load later (the page's resource timings)
  var NAME = /["'`]((?:X-)?[A-Za-z][A-Za-z0-9-]{1,48}(?:csrf|xsrf|forgery|verification)[A-Za-z0-9-]{0,24})["'`]/gi;
  var scanned = {};
  function names(text) {
    var m;
    text = String(text || "").slice(0, 4 << 20);
    NAME.lastIndex = 0;
    while ((m = NAME.exec(text))) {
      var n = m[1];
      if (/^[A-Za-z][A-Za-z0-9-]*$/.test(n) && seen.names.indexOf(n) < 0 && seen.names.length < 12) seen.names.push(n);
    }
  }
  function scanScripts() {
    var srcs = [];
    function add(href) {
      try {
        var u = new URL(href, location.href);
        if (u.origin === location.origin && !scanned[u.href] && u.pathname.indexOf("/__o77w/") !== 0 && srcs.length < 12) { scanned[u.href] = 1; srcs.push(u.href); }
      } catch (_) { /* */ }
    }
    try {
      var ss = document.querySelectorAll("script");
      for (var i = 0; i < ss.length; i++) { if (ss[i].src) add(ss[i].src); else names(ss[i].textContent); }
      var pl = document.querySelectorAll("link[rel=modulepreload][href], link[rel=preload][as=script][href]");
      for (var j = 0; j < pl.length; j++) add(pl[j].href);
      if (window.performance && performance.getEntriesByType) {
        performance.getEntriesByType("resource").forEach(function (e) { if (e.initiatorType === "script" || /\.m?js(\?|$)/.test(e.name)) add(e.name); });
      }
    } catch (_) { /* */ }
    srcs.forEach(function (href) {
      origFetch.call(window, href, { credentials: "same-origin" }).then(function (r) { return r.ok ? r.text() : ""; }).then(names).catch(function () { /* */ });
    });
  }

  var origFetch = window.fetch;
  if (origFetch) {
    window.fetch = function (input, init) {
      var path = null;
      try {
        var url = typeof input === "string" ? input : (input && input.url) || String(input);
        path = apiPath(url);
        if (path) {
          var hdrs = plain(init && init.headers);
          if (input && typeof input === "object" && input.headers && !(init && init.headers)) hdrs = plain(input.headers);
          note(((init && init.method) || (input && input.method) || "GET").toUpperCase(), path, hdrs, init && init.body);
        }
      } catch (_) { /* never in the panel's way */ }
      var p = origFetch.apply(this, arguments);
      if (path) {
        p.then(function (r) {
          try {
            if ((r.headers.get("Content-Type") || "").indexOf("json") >= 0) r.clone().json().then(function (v) { scanAnswer(v, 0); }).catch(function () {});
          } catch (_) { /* */ }
        }, function () {});
      }
      return p;
    };
  }
  var X = window.XMLHttpRequest && window.XMLHttpRequest.prototype;
  if (X) {
    var open = X.open, setH = X.setRequestHeader, send = X.send;
    X.open = function (method, url) {
      try { this.__o77w = { method: String(method || "GET").toUpperCase(), path: apiPath(url), headers: {} }; } catch (_) { /* */ }
      return open.apply(this, arguments);
    };
    X.setRequestHeader = function (k, v) {
      try { if (this.__o77w) this.__o77w.headers[k] = v; } catch (_) { /* */ }
      return setH.apply(this, arguments);
    };
    X.send = function (body) {
      try {
        var r = this.__o77w;
        if (r && r.path) {
          note(r.method, r.path, r.headers, body);
          this.addEventListener("load", function () {
            try { if ((this.getResponseHeader("Content-Type") || "").indexOf("json") >= 0) scanAnswer(JSON.parse(this.responseText), 0); } catch (_) { /* */ }
          });
        }
      } catch (_) { /* */ }
      return send.apply(this, arguments);
    };
  }
  // A page that cannot scroll but is taller than the app's frame gets a scrollbar.  Warden's pages do
  // not scroll their body (body { overflow: hidden }: the panel scrolls its own views), and its first-run
  // setup is a centred column at least as tall as the window: in a small window the end of the form --
  // the confirm-password box, the button -- is out of reach.  So when the body is clipped and what it
  // holds is taller than the frame, the body scrolls (vertically only); once it fits again (the setup
  // done, the panel's own layout back) it is left exactly as the page made it.
  var unclipped = false, unclipTimer = 0;
  function unclip() {
    unclipTimer = 0;
    try {
      var b = document.body, de = document.documentElement;
      if (!b) return;
      var tall = b.scrollHeight > window.innerHeight + 1;
      if (tall && !unclipped && getComputedStyle(b).overflowY === "hidden") {
        b.style.setProperty("overflow-y", "auto", "important");
        if (getComputedStyle(de).overflowY === "hidden") de.style.setProperty("overflow-y", "auto", "important");
        unclipped = true;
      } else if (!tall && unclipped) {
        b.style.removeProperty("overflow-y");
        de.style.removeProperty("overflow-y");
        unclipped = false;
      }
      seen.unclipped = unclipped;
    } catch (_) { /* never in the panel's way */ }
  }
  function soon() { if (!unclipTimer) unclipTimer = setTimeout(unclip, 120); }
  function watchLayout() {
    unclip();
    window.addEventListener("resize", soon);
    try {
      new MutationObserver(soon).observe(document.documentElement,
        { childList: true, subtree: true, attributes: true, attributeFilter: ["hidden", "class", "style", "open"] });
    } catch (_) { /* */ }
    setInterval(unclip, 1500);         // (a layout change no mutation tells of: fonts, images)
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { scanPage(); scanScripts(); watchLayout(); });
  else { scanPage(); scanScripts(); watchLayout(); }
  setInterval(scanPage, 5000);
  setTimeout(scanScripts, 4000);       // (the chunks a panel loads once it runs)
  setTimeout(scanScripts, 20000);
})();

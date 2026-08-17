/**
 * HeatmapCollector — offline-friendly user interaction collector.
 *
 * Captures clicks, route/element dwell time, and view events as
 * `heatmap-events/1` objects (see docs/LOG_FORMAT_SPEC.md) and ships them in
 * batches to the application's OWN backend on the private network. No external
 * calls, no dependencies, no input/text capture.
 *
 * Usage:
 *   HeatmapCollector.init({ endpoint: "/api/v1/ux-events", app: "2.14.0" });
 *
 * Tag elements you want stable analytics for:
 *   <button data-track-id="orders.export">Export</button>
 */
(function (global) {
  "use strict";

  var SCHEMA_V = 1;
  var STORE_KEY = "hmc.spill";       // localStorage spill buffer for failed sends
  var STORE_CAP = 500 * 1024;        // ~500 KB spill cap
  var DWELL_HEARTBEAT_MS = 30000;    // flush long dwells every 30 s

  var cfg = null;
  var queue = [];
  var flushTimer = null;
  var sid = null;
  var currentRoute = null;
  var routeVisibleSince = null;      // ts when current route became visible
  var elDwell = {};                  // key -> { el, visibleSince, acc }
  var observer = null;
  var sampledOut = false;

  // ---------------------------------------------------------------- utilities

  function now() { return Date.now(); }

  function uuid() {
    if (global.crypto && crypto.randomUUID) return crypto.randomUUID();
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0;
      return (c === "x" ? r : (r & 3) | 8).toString(16);
    });
  }

  function round4(n) { return Math.round(n * 10000) / 10000; }

  function viewport() {
    return { w: global.innerWidth || 0, h: global.innerHeight || 0 };
  }

  function route() {
    if (cfg.route) {
      var r = cfg.route();                     // app-supplied router template
      if (typeof r === "string" && r) return r;
    }
    return location.pathname || "/";           // query string never included
  }

  // Element identity per spec §4: tid -> id -> generated selector.
  function elementInfo(node) {
    if (!node || node.nodeType !== 1) return null;
    var el = node;
    // Attribute clicks to the nearest tagged ancestor when one exists — an SVG
    // icon inside a tagged button should count for the button.
    var tagged = el.closest ? el.closest("[data-track-id]") : null;
    if (tagged) el = tagged;

    var info = {};
    var tid = el.getAttribute && el.getAttribute("data-track-id");
    if (tid) info.tid = tid;
    else if (el.id && !/[:\d]{2,}|^radix|^mui|^ember|^react/i.test(el.id)) info.id = el.id;
    else info.sel = shortSelector(el);

    var r = el.getBoundingClientRect();
    var vp = viewport();
    if (vp.w > 0 && vp.h > 0) {
      info.rect = [round4(r.left / vp.w), round4(r.top / vp.h),
                   round4(r.width / vp.w), round4(r.height / vp.h)];
    }
    return info;
  }

  function shortSelector(el) {
    var parts = [];
    var node = el;
    while (node && node.nodeType === 1 && parts.length < 4 &&
           node !== document.body && node !== document.documentElement) {
      var part = node.tagName.toLowerCase();
      var cls = (typeof node.className === "string" ? node.className : "")
        .trim().split(/\s+/).filter(function (c) {
          return c && c.length < 32 && !/^[a-z]*[-_][a-z0-9]{5,}$/i.test(c); // skip hashed classes
        })[0];
      if (cls) part += "." + cls;
      var parent = node.parentElement;
      if (parent) {
        var same = parent.querySelectorAll(":scope > " + node.tagName).length;
        if (same > 1) {
          var idx = Array.prototype.indexOf.call(
            parent.querySelectorAll(":scope > " + node.tagName), node) + 1;
          part += ":nth-of-type(" + idx + ")";
        }
      }
      parts.unshift(part);
      node = parent;
    }
    return parts.join(">");
  }

  // ------------------------------------------------------------------ events

  function base(type) {
    var e = { v: SCHEMA_V, t: type, ts: now(), sid: sid,
              route: currentRoute, vp: viewport() };
    if (cfg.app) e.app = cfg.app;
    return e;
  }

  function push(event) {
    queue.push(event);
    if (queue.length >= cfg.maxBatch) flush(false);
  }

  function onClick(ev) {
    var el = elementInfo(ev.target);
    if (!el) return;
    var vp = viewport();
    if (vp.w <= 0 || vp.h <= 0) return;
    var e = base("click");
    e.x = round4(ev.clientX / vp.w);
    e.y = round4(ev.clientY / vp.h);
    e.el = el;
    push(e);
  }

  // ------------------------------------------------------------------- dwell

  function startRouteDwell() { routeVisibleSince = now(); }

  function flushRouteDwell() {
    if (routeVisibleSince == null) return;
    var ms = now() - routeVisibleSince;
    routeVisibleSince = null;
    if (ms < 100) return;
    var e = base("dwell");
    e.ms = ms;
    push(e);
  }

  function flushElementDwell(key, entry) {
    var acc = entry.acc;
    if (entry.visibleSince != null) {
      acc += now() - entry.visibleSince;
      entry.visibleSince = now();
    }
    entry.acc = 0;
    if (acc < 250) return;
    var e = base("dwell");
    e.ms = acc;
    e.el = entry.info;
    push(e);
  }

  function flushAllDwell() {
    flushRouteDwell();
    if (document.visibilityState === "visible") startRouteDwell();
    for (var k in elDwell) flushElementDwell(k, elDwell[k]);
  }

  function observeTagged() {
    if (!global.IntersectionObserver) return;
    if (observer) observer.disconnect();
    elDwell = {};
    observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        var tid = en.target.getAttribute("data-track-id");
        if (!tid) return;
        var entry = elDwell[tid];
        if (!entry) {
          entry = elDwell[tid] = { acc: 0, visibleSince: null,
                                   info: elementInfo(en.target) };
        }
        if (en.isIntersecting && en.intersectionRatio >= 0.5) {
          if (entry.visibleSince == null) entry.visibleSince = now();
        } else if (entry.visibleSince != null) {
          entry.acc += now() - entry.visibleSince;
          entry.visibleSince = null;
        }
      });
    }, { threshold: [0, 0.5] });
    var tagged = document.querySelectorAll("[data-track-id]");
    for (var i = 0; i < tagged.length; i++) observer.observe(tagged[i]);
  }

  // ------------------------------------------------------------------- route

  function onRouteChange() {
    var next = route();
    if (next === currentRoute) return;
    flushRouteDwell();
    for (var k in elDwell) flushElementDwell(k, elDwell[k]);
    currentRoute = next;
    if (document.visibilityState === "visible") startRouteDwell();
    push(base("view"));
    // DOM for the new route may mount asynchronously.
    setTimeout(observeTagged, 250);
  }

  function hookHistory() {
    ["pushState", "replaceState"].forEach(function (m) {
      var orig = history[m];
      history[m] = function () {
        var r = orig.apply(this, arguments);
        onRouteChange();
        return r;
      };
    });
    global.addEventListener("popstate", onRouteChange);
    global.addEventListener("hashchange", onRouteChange);
  }

  // --------------------------------------------------------------- transport

  function spillLoad() {
    try { return JSON.parse(localStorage.getItem(STORE_KEY) || "[]"); }
    catch (e) { return []; }
  }

  function spillSave(events) {
    try {
      var s = JSON.stringify(events);
      while (s.length > STORE_CAP && events.length) {   // drop oldest first
        events.splice(0, Math.ceil(events.length / 4));
        s = JSON.stringify(events);
      }
      localStorage.setItem(STORE_KEY, s);
    } catch (e) { /* storage full/blocked: accept the loss */ }
  }

  function send(events, isUnload) {
    var body = JSON.stringify({ events: events });
    if (isUnload && navigator.sendBeacon) {
      var ok = navigator.sendBeacon(cfg.endpoint,
        new Blob([body], { type: "application/json" }));
      if (!ok) spillSave(spillLoad().concat(events));
      return;
    }
    fetch(cfg.endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body,
      keepalive: isUnload === true
    }).catch(function () {
      spillSave(spillLoad().concat(events));
    });
  }

  function flush(isUnload) {
    if (isUnload) flushAllDwell();
    var spilled = spillLoad();
    if (spilled.length) { localStorage.removeItem(STORE_KEY); }
    var batch = spilled.concat(queue.splice(0, queue.length));
    if (!batch.length) return;
    send(batch, isUnload);
  }

  // -------------------------------------------------------------- lifecycle

  function onVisibility() {
    if (document.visibilityState === "hidden") {
      flushAllDwell();
      flush(true);
    } else {
      startRouteDwell();
    }
  }

  // ------------------------------------------------------------------- init

  var api = {
    init: function (options) {
      if (cfg) return api;                       // idempotent
      cfg = Object.assign({
        endpoint: "/api/v1/ux-events",
        app: null,
        route: null,                             // () => "/orders/:id"
        flushIntervalMs: 10000,
        maxBatch: 50,
        sampleRate: 1.0
      }, options || {});

      if (Math.random() >= cfg.sampleRate) { sampledOut = true; return api; }

      try { sid = sessionStorage.getItem("hmc.sid"); } catch (e) { /* blocked */ }
      if (!sid) {
        sid = uuid();
        try { sessionStorage.setItem("hmc.sid", sid); } catch (e) { /* blocked */ }
      }

      currentRoute = route();
      document.addEventListener("click", onClick, true);
      document.addEventListener("visibilitychange", onVisibility);
      global.addEventListener("pagehide", function () { flush(true); });
      hookHistory();

      push(base("view"));
      if (document.visibilityState === "visible") startRouteDwell();
      observeTagged();

      flushTimer = setInterval(function () { flush(false); }, cfg.flushIntervalMs);
      setInterval(flushAllDwell, DWELL_HEARTBEAT_MS);
      return api;
    },

    /** Re-scan the DOM for [data-track-id] elements (call after big UI updates
     *  if your router integration isn't enough). */
    rescan: observeTagged,

    /** Flush pending events immediately. */
    flush: function () { flush(false); },

    /** True if this session was sampled out and records nothing. */
    isSampledOut: function () { return sampledOut; }
  };

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else global.HeatmapCollector = api;
})(typeof window !== "undefined" ? window : this);

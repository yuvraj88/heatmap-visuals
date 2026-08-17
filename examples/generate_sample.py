#!/usr/bin/env python3
"""Generate a realistic sample heatmap-events/1 NDJSON log for demos/testing.

    python3 examples/generate_sample.py > examples/sample-heatmap.ndjson
"""

import json
import random
import sys
import uuid

random.seed(20260817)

APP = "2.14.0"
VIEWPORTS = [(1440, 900), (1920, 1080), (1366, 768), (1280, 800)]

# Per route: elements as (key_kind, key, rect[x,y,w,h], click_weight, dwell_weight)
ROUTES = {
    "/dashboard": {
        "weight": 5,
        "elements": [
            ("tid", "nav.dashboard",     [0.00, 0.00, 0.12, 0.06], 2, 1),
            ("tid", "nav.orders",        [0.00, 0.07, 0.12, 0.05], 8, 1),
            ("tid", "nav.settings",      [0.00, 0.13, 0.12, 0.05], 1, 0),
            ("tid", "dash.kpi-row",      [0.15, 0.05, 0.82, 0.14], 1, 6),
            ("tid", "dash.alerts-panel", [0.15, 0.22, 0.40, 0.50], 6, 9),
            ("tid", "dash.refresh",      [0.90, 0.05, 0.07, 0.05], 9, 0),
            ("sel", "main>div.chart-area>canvas", [0.58, 0.22, 0.39, 0.50], 2, 5),
        ],
        "dwell_s": (25, 120),
    },
    "/orders": {
        "weight": 4,
        "elements": [
            ("tid", "nav.dashboard",  [0.00, 0.00, 0.12, 0.06], 2, 0),
            ("tid", "orders.search",  [0.15, 0.05, 0.30, 0.05], 7, 2),
            ("tid", "orders.export",  [0.78, 0.05, 0.09, 0.045], 5, 0),
            ("tid", "orders.new",     [0.89, 0.05, 0.08, 0.045], 3, 0),
            ("tid", "orders.table",   [0.15, 0.14, 0.82, 0.72], 10, 12),
            ("sel", "main>nav.pagination>button:nth-of-type(2)",
                                      [0.52, 0.90, 0.04, 0.04], 4, 0),
        ],
        "dwell_s": (40, 300),
    },
    "/settings": {
        "weight": 1,
        "elements": [
            ("tid", "settings.tabs",   [0.15, 0.05, 0.60, 0.05], 5, 1),
            ("tid", "settings.form",   [0.15, 0.14, 0.55, 0.60], 4, 8),
            ("tid", "settings.save",   [0.15, 0.78, 0.10, 0.05], 6, 0),
            ("id",  "advanced-toggle", [0.60, 0.78, 0.12, 0.05], 1, 0),
        ],
        "dwell_s": (15, 90),
    },
}


def clamp01(v):
    return max(0.0, min(1.0, v))


def click_point(rect):
    """Gaussian around the element center, mostly inside the rect."""
    x, y, w, h = rect
    return (round(clamp01(random.gauss(x + w / 2, w / 4)), 4),
            round(clamp01(random.gauss(y + h / 2, h / 4)), 4))


def el_obj(kind, key, rect):
    return {kind: key, "rect": rect}


# /signup form fields, in fill order:
# (name, ftype, rect, mean focus seconds, mean edits, P(abandon before this field))
SIGNUP_FIELDS = [
    ("fullname", "text",     [0.35, 0.22, 0.30, 0.05],  6, 12, 0.00),
    ("email",    "email",    [0.35, 0.30, 0.30, 0.05],  8, 18, 0.03),
    ("company",  "text",     [0.35, 0.38, 0.30, 0.05], 14, 15, 0.22),
    ("role",     "select",   [0.35, 0.46, 0.30, 0.05],  9,  2, 0.10),
    ("password", "password", [0.35, 0.54, 0.30, 0.05], 11, 22, 0.08),
]
SIGNUP_SUBMIT = ("tid", "signup.submit", [0.35, 0.64, 0.14, 0.055])


def signup_session(emit_fn, ts):
    """One signup attempt: field-by-field fill with a chance to abandon."""
    touched, filled, total_ms = 0, 0, 0
    abandoned = False
    for name, ftype, rect, mean_s, mean_edits, p_abandon in SIGNUP_FIELDS:
        if random.random() < p_abandon:
            abandoned = True
            break
        ts += random.randint(500, 3_000)
        emit_fn(ts, {"t": "click", "x": round(clamp01(rect[0] + rect[2] / 2), 4),
                     "y": round(clamp01(rect[1] + rect[3] / 2), 4),
                     "el": {"name": name, "rect": rect}})
        ms = max(400, int(random.gauss(mean_s, mean_s / 3) * 1000))
        edits = max(0, int(random.gauss(mean_edits, mean_edits / 3)))
        ts += ms
        is_filled = random.random() > 0.06
        emit_fn(ts, {"t": "field", "ms": ms, "edits": edits, "filled": is_filled,
                     "ftype": ftype, "el": {"name": name, "rect": rect},
                     "form": "signup"})
        touched += 1
        filled += 1 if is_filled else 0
        total_ms += ms
    ts += random.randint(500, 4_000)
    if not abandoned:
        kind, key, rect = SIGNUP_SUBMIT
        emit_fn(ts, {"t": "click", "x": round(clamp01(rect[0] + rect[2] / 2), 4),
                     "y": round(clamp01(rect[1] + rect[3] / 2), 4),
                     "el": {kind: key, "rect": rect}})
    emit_fn(ts, {"t": "form", "form": "signup",
                 "outcome": "abandon" if abandoned else "submit",
                 "touched": touched, "filled": filled, "ms": total_ms,
                 "fields": len(SIGNUP_FIELDS)})
    emit_fn(ts, {"t": "dwell", "ms": total_ms + random.randint(3_000, 15_000)})
    return ts


def main():
    out = sys.stdout
    ts = 1755400000000  # 2026-08-17 morning, UTC
    route_names = list(ROUTES)
    route_weights = [ROUTES[r]["weight"] for r in route_names]

    for _session in range(160):
        sid = str(uuid.uuid4())
        vp_w, vp_h = random.choice(VIEWPORTS)
        vp = {"w": vp_w, "h": vp_h}
        ts += random.randint(30_000, 900_000)

        # ~40 % of sessions attempt the signup form
        if random.random() < 0.4:
            def emit_signup(ev_ts, extra):
                ev = {"v": 1, "ts": ev_ts, "sid": sid, "route": "/signup",
                      "vp": vp, "app": APP}
                ev.update(extra)
                out.write(json.dumps(ev, separators=(",", ":")) + "\n")
            emit_signup(ts, {"t": "view"})
            ts = signup_session(lambda ev_ts, extra: emit_signup(ev_ts, extra), ts)

        for _visit in range(random.randint(1, 4)):
            route = random.choices(route_names, weights=route_weights)[0]
            spec = ROUTES[route]

            def emit(extra):
                ev = {"v": 1, "ts": ts, "sid": sid, "route": route,
                      "vp": vp, "app": APP}
                ev.update(extra)
                out.write(json.dumps(ev, separators=(",", ":")) + "\n")

            emit({"t": "view"})
            dwell_total = random.randint(*spec["dwell_s"]) * 1000

            n_clicks = random.randint(2, 12)
            weights = [e[3] for e in spec["elements"]]
            for _ in range(n_clicks):
                ts += random.randint(800, 15_000)
                kind, key, rect, _cw, _dw = random.choices(
                    spec["elements"], weights=weights)[0]
                x, y = click_point(rect)
                emit({"t": "click", "x": x, "y": y,
                      "el": el_obj(kind, key, rect)})

            ts += random.randint(1_000, 20_000)
            emit({"t": "dwell", "ms": dwell_total})
            for kind, key, rect, _cw, dw in spec["elements"]:
                if dw <= 0:
                    continue
                ms = int(dwell_total * dw / 20 * random.uniform(0.5, 1.0))
                if ms >= 250:
                    emit({"t": "dwell", "ms": ms,
                          "el": el_obj(kind, key, rect)})

    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Aggregate heatmap-events/1 NDJSON logs into a compact heatmap-aggregate/1 JSON.

Stdlib only — runs on any machine with Python 3.8+, no network, no pip.

Examples:
    python3 process_heatmap_logs.py logs/heatmap-interactions-*.ndjson* -o agg.json
    python3 process_heatmap_logs.py bundle/heatmap/ --route "/orders*" \
        --from 2026-08-01 --to 2026-08-17 -o agg.json

Accepts .ndjson / .log / .gz files, directories (scanned recursively), and
shell globs. Output format is documented in docs/LOG_FORMAT_SPEC.md §5.
"""

import argparse
import datetime as dt
import fnmatch
import glob
import gzip
import io
import json
import os
import sys
from collections import Counter, defaultdict

SCHEMA_IN = 1
SCHEMA_OUT = "heatmap-aggregate/1"


def iter_files(inputs):
    for item in inputs:
        paths = glob.glob(item) or [item]
        for path in paths:
            if os.path.isdir(path):
                for root, _dirs, files in os.walk(path):
                    for name in sorted(files):
                        if name.endswith((".ndjson", ".log", ".ndjson.gz", ".log.gz")):
                            yield os.path.join(root, name)
            elif os.path.isfile(path):
                yield path
            else:
                print(f"warning: no such input: {item}", file=sys.stderr)


def open_maybe_gzip(path):
    if path.endswith(".gz"):
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8", errors="replace")
    return open(path, encoding="utf-8", errors="replace")


def parse_date_ms(value, end_of_day=False):
    day = dt.datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    if end_of_day:
        day += dt.timedelta(days=1)
    return int(day.timestamp() * 1000)


def element_key(el):
    """Spec §4: identity precedence tid -> id -> sel."""
    for kind in ("tid", "id", "sel"):
        value = el.get(kind)
        if isinstance(value, str) and value:
            return value, kind
    return None, None


class RouteAgg:
    def __init__(self, cols, rows):
        self.cols, self.rows = cols, rows
        self.grid = [[0] * cols for _ in range(rows)]
        self.views = 0
        self.clicks = 0
        self.dwell_ms = 0
        self.sessions = set()
        self.viewports = Counter()
        # key -> {clicks, dwellMs, kind, rect sums for weighted mean}
        self.elements = defaultdict(lambda: {
            "clicks": 0, "dwellMs": 0, "kind": None,
            "_rect": [0.0, 0.0, 0.0, 0.0], "_rect_n": 0,
        })

    def add_rect(self, entry, rect):
        if (isinstance(rect, list) and len(rect) == 4
                and all(isinstance(v, (int, float)) for v in rect)):
            for i in range(4):
                entry["_rect"][i] += rect[i]
            entry["_rect_n"] += 1

    def to_json(self):
        elements = {}
        for key, e in sorted(self.elements.items(),
                             key=lambda kv: (-kv[1]["clicks"], -kv[1]["dwellMs"])):
            out = {"clicks": e["clicks"], "dwellMs": e["dwellMs"], "kind": e["kind"]}
            if e["_rect_n"]:
                out["rect"] = [round(v / e["_rect_n"], 4) for v in e["_rect"]]
            elements[key] = out
        vp = None
        if self.viewports:
            w, h = self.viewports.most_common(1)[0][0]
            vp = {"w": w, "h": h}
        return {
            "views": self.views,
            "sessions": len(self.sessions),
            "clicks": self.clicks,
            "dwellMs": self.dwell_ms,
            "vp": vp,
            "clickGrid": self.grid,
            "elements": elements,
        }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("inputs", nargs="+", help="NDJSON files, globs, or directories")
    ap.add_argument("-o", "--output", default="heatmap-aggregate.json")
    ap.add_argument("--route", help='route filter, glob allowed (e.g. "/orders*")')
    ap.add_argument("--from", dest="date_from", metavar="YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", metavar="YYYY-MM-DD",
                    help="inclusive end date")
    ap.add_argument("--grid", default="48x27", metavar="COLSxROWS")
    args = ap.parse_args(argv)

    try:
        cols, rows = (int(v) for v in args.grid.lower().split("x"))
        assert 1 <= cols <= 512 and 1 <= rows <= 512
    except (ValueError, AssertionError):
        ap.error("--grid must look like 48x27")

    ts_min = parse_date_ms(args.date_from) if args.date_from else None
    ts_max = parse_date_ms(args.date_to, end_of_day=True) if args.date_to else None

    routes = defaultdict(lambda: RouteAgg(cols, rows))
    files = events = skipped = 0
    seen_min = seen_max = None

    for path in iter_files(args.inputs):
        files += 1
        with open_maybe_gzip(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    skipped += 1
                    continue
                if not isinstance(ev, dict) or ev.get("v") != SCHEMA_IN:
                    skipped += 1
                    continue
                etype, ts, route = ev.get("t"), ev.get("ts"), ev.get("route")
                if etype not in ("click", "dwell", "view") \
                        or not isinstance(ts, int) or not isinstance(route, str):
                    skipped += 1
                    continue
                if (ts_min and ts < ts_min) or (ts_max and ts >= ts_max):
                    continue
                if args.route and not fnmatch.fnmatch(route, args.route):
                    continue

                events += 1
                seen_min = ts if seen_min is None else min(seen_min, ts)
                seen_max = ts if seen_max is None else max(seen_max, ts)
                agg = routes[route]
                if isinstance(ev.get("sid"), str):
                    agg.sessions.add(ev["sid"])
                vp = ev.get("vp")
                if isinstance(vp, dict) and isinstance(vp.get("w"), int) \
                        and isinstance(vp.get("h"), int) and vp["w"] > 0 and vp["h"] > 0:
                    agg.viewports[(vp["w"], vp["h"])] += 1

                if etype == "view":
                    agg.views += 1
                elif etype == "click":
                    x, y = ev.get("x"), ev.get("y")
                    if isinstance(x, (int, float)) and isinstance(y, (int, float)) \
                            and 0 <= x <= 1 and 0 <= y <= 1:
                        agg.clicks += 1
                        col = min(int(x * cols), cols - 1)
                        row = min(int(y * rows), rows - 1)
                        agg.grid[row][col] += 1
                    el = ev.get("el")
                    if isinstance(el, dict):
                        key, kind = element_key(el)
                        if key:
                            entry = agg.elements[key]
                            entry["clicks"] += 1
                            entry["kind"] = entry["kind"] or kind
                            agg.add_rect(entry, el.get("rect"))
                elif etype == "dwell":
                    ms = ev.get("ms")
                    if not isinstance(ms, int) or ms < 0:
                        skipped += 1
                        continue
                    el = ev.get("el")
                    if isinstance(el, dict):
                        key, kind = element_key(el)
                        if key:
                            entry = agg.elements[key]
                            entry["dwellMs"] += ms
                            entry["kind"] = entry["kind"] or kind
                            agg.add_rect(entry, el.get("rect"))
                    else:
                        agg.dwell_ms += ms

    out = {
        "schema": SCHEMA_OUT,
        "generatedAt": dt.datetime.now(dt.timezone.utc)
                         .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": {"files": files, "events": events, "skipped": skipped,
                   "from": seen_min, "to": seen_max},
        "grid": {"cols": cols, "rows": rows},
        "routes": {route: agg.to_json() for route, agg in sorted(routes.items())},
    }
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(out, fh, separators=(",", ":"))
    print(f"{args.output}: {len(routes)} route(s), {events} events "
          f"({skipped} skipped) from {files} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# Proposal: Offline User Interaction Heatmap for Restricted Environments

**Status:** Proposed · **Audience:** engineering + product · **Scope:** web UI of the application

## 0. Summary

We add a small, zero-dependency JavaScript collector to the application UI that
records clicks and time-spent as structured events. The application backend
appends these events to a dedicated NDJSON log file that is rotated and shipped
inside the **existing log download bundle**, exactly like API/audit/pod logs.
Outside the restricted network, an analyst opens a **self-contained HTML
visualizer** (no install, no internet) and drops the downloaded log file in to
see click-density and time-spent heatmaps per screen, optionally overlaid on a
screenshot of the real UI. A stdlib-only Python aggregator is provided for large
log volumes and for feeding other tools.

Design constraints honored throughout:

- **No network egress** from the deployment environment — data leaves only via
  the log download users already perform.
- **No new infrastructure** — no database, no analytics service, no build step
  for the viewer.
- **Privacy by construction** — positions and element identities only; never
  input values, text content, or personal identifiers.

---

## 1. Data collection strategy

### 1.1 What we capture

| Event | Trigger | Answers |
|---|---|---|
| `click` | any `pointerdown`→`click` on the document (capture phase) | *Where do users click?* Both viewport-normalized coordinates and the element that was hit. |
| `dwell` (route) | route change, tab hide, page unload | *How long do users spend on each screen?* Accumulated visible time per route. |
| `dwell` (element) | `IntersectionObserver` on tagged elements | *Which areas are on screen the longest?* Visible-time per `data-track-id` element. |
| `view` | route change / initial load | Denominator for rates; carries viewport size for coordinate reconstruction. |
| `field` | form field loses focus (plus a `change` fallback for autofill/programmatic changes) | *Are users filling the forms, and where do they spend the actual time?* Focused milliseconds, edit-event count, and a filled-or-not **boolean** per field — never the value. |
| `form` | form submit, or route change / page hide with touched fields | *Do form attempts complete?* Outcome (`submit`/`abandon`), fields touched vs filled, total focused time. |

### 1.1.1 Retrofitting a product that is already running

No per-component changes are required. All capture is **document-level event
delegation** (`click`, `focusin`/`focusout`, `input`, `change`, `submit` in the
capture phase), so integration is one `<script>` tag in the existing app shell
plus one backend route. Form fields are identified by the `name`/`id`
attributes they already have; `data-track-id` is an optional improvement for
long-term stability, not a prerequisite. Roll out behind a feature flag and the
running product is otherwise untouched.

Coordinates are stored **normalized to the viewport** (`x, y ∈ [0,1]`) together
with the viewport size, so sessions with different window sizes aggregate
sensibly and the visualizer can re-project onto any canvas size. For clicks we
additionally store the target element's identity and its normalized bounding
box, which enables element-level (rather than pixel-level) analysis — far more
robust against responsive layout differences.

### 1.2 Associating interactions with UI elements

Resolution order for element identity:

1. **`data-track-id`** — teams annotate the elements they care about
   (`<button data-track-id="orders.export">`). Stable across releases and DOM
   refactors. Recommended for all primary navigation and actions.
2. **`id`** attribute, if present and not framework-generated.
3. **Generated CSS selector** — a short structural path
   (`main > div.toolbar > button:nth-of-type(2)`, max 4 levels) as a fallback so
   *every* click is attributable even on untagged UI.

The element's bounding box (normalized) is recorded with each event, so the
visualizer can draw element overlays without access to the application DOM.

### 1.3 Working offline: buffering and transport

The collector never talks to the outside world — its only destination is the
application's **own backend** on the same private network:

- Events buffer in memory and flush every 10 s or 50 events (whichever first)
  via `navigator.sendBeacon` (falls back to `fetch` with `keepalive`), so
  page unloads don't lose data.
- If the backend is briefly unreachable, batches spill to `localStorage`
  (capped at ~500 KB, oldest dropped first) and retry on the next flush.
- A `sampleRate` option (default 1.0) allows probabilistic sampling per session
  if volume ever becomes a concern.

### 1.4 Privacy & footprint

- Captured: timestamps, route paths, normalized coordinates, element
  selectors/track-ids/field names, durations, edit-event counts, filled-or-not
  booleans, viewport size, a random per-browser-session UUID (rotates when the
  tab session ends; never derived from the user).
- **Never captured:** keystrokes, input values, text content, URLs' query
  strings (stripped), user names/IDs, IP addresses. For form fields the single
  value-derived datum is the boolean "left non-empty / checked"; the value is
  read in exactly one collector function and only that boolean leaves it.
- Overhead: the collector is ~4 KB minified, passive listeners only; typical
  volume is 2–10 KB of NDJSON per user-session.

---

## 2. Log integration

### 2.1 A dedicated log file, not an extension of existing logs

Interaction events go to their **own file family** rather than being interleaved
into API/audit logs, because (a) they have a different retention/volume profile,
(b) the audit log's integrity guarantees shouldn't be diluted with UX telemetry,
and (c) a dedicated file lets the download UI offer "include UX heatmap data" as
a checkbox and lets the visualizer consume the file without filtering.

```
<existing log dir>/
  api-2026-08-17.log
  audit-2026-08-17.log
  heatmap/
    heatmap-interactions-2026-08-17.ndjson       ← new
    heatmap-interactions-2026-08-16.ndjson.gz    ← rotated + gzipped
```

Rotation: daily, plus a size cap (default 50 MB/file); compress on rotation;
retain N days (default 14) — reuse whatever rotation utility the other logs use.
The existing "download logs" flow simply includes the `heatmap/` directory.

**All interaction event types share this one file** — clicks, dwell, views,
form-field engagement, and form outcomes are lines in the same NDJSON stream,
so a single downloaded file carries the complete picture. If a literal single
file is preferred over daily rotation, rotate by size only; the processor and
visualizer accept one file or many interchangeably (concatenation is also
valid NDJSON: `cat *.ndjson > all.ndjson`).

### 2.2 Format: NDJSON (`heatmap-events/1`)

One JSON object per line — the normative field-by-field spec is in
[`LOG_FORMAT_SPEC.md`](./LOG_FORMAT_SPEC.md). NDJSON was chosen over CSV
(nested fields: rects, viewport) and over a custom format (every language parses
it; `jq`/`grep` work; appends are atomic per line; a truncated last line —
possible on crash — costs exactly one event).

### 2.3 Backend ingestion endpoint

One route, ~15 lines, in the existing backend (Node/Express shown; the shape is
identical in any stack):

```js
// POST /api/v1/ux-events  — body: {"events": [ ...heatmap-events/1 objects ]}
app.post("/api/v1/ux-events", express.json({ limit: "256kb" }), (req, res) => {
  const events = Array.isArray(req.body?.events) ? req.body.events : [];
  const day = new Date().toISOString().slice(0, 10);
  const lines = events
    .filter(e => e && e.v === 1 && typeof e.t === "string")   // schema gate
    .map(e => JSON.stringify(e) + "\n").join("");
  if (lines) fs.appendFile(`${LOG_DIR}/heatmap/heatmap-interactions-${day}.ndjson`,
                           lines, () => {});
  res.status(204).end();          // fire-and-forget; never block the UI
});
```

Server responsibilities are deliberately minimal: schema-version gate, append,
rotate. No parsing, no aggregation, no state — the heavy lifting happens in the
analyst's local environment.

---

## 3. Data processing & visualization (local environment)

### 3.1 Two consumption paths

**Path A — drop the raw log straight into the visualizer** (default).
`visualizer/index.html` parses `heatmap-events/1` NDJSON itself and aggregates
in the browser. Practical up to a few hundred thousand events (~50 MB) —
covering the common "one bundle from one site" case with zero tooling.

**Path B — pre-aggregate with the Python processor** (large volumes, many
sites, or feeding other tools):

```bash
python3 processor/process_heatmap_logs.py \
    logs/heatmap/heatmap-interactions-*.ndjson* \      # .ndjson and .ndjson.gz
    --from 2026-08-01 --to 2026-08-17 \
    --route "/orders*" \
    -o heatmap-aggregate.json
```

Stdlib only (no `pip install` — analysts' machines may be locked down too).
It merges any number of files, filters by date/route, and emits a compact
`heatmap-aggregate/1` JSON: per route, a click-density grid (default 48×27
cells over the normalized viewport), per-element click counts and dwell
totals, session/view counts. The visualizer accepts this file interchangeably
with raw logs; it is also a stable interface for anyone who prefers a
notebook or BI tool.

### 3.2 The visualizer

A **single self-contained HTML file** — inline CSS/JS, no CDN, no build, opens
from `file://` on any modern browser. This is the strongest possible fit for the
constraint: the "tool install" is copying one file.

Capabilities:

- **Input:** drag-and-drop or file-pick raw `.ndjson`/`.log` files (multiple at
  once) or a `heatmap-aggregate.json`.
- **Click-density view:** kernel-stamped density rendered on `<canvas>`,
  colored with a single-hue sequential blue ramp (light = few, dark = many) —
  areas of high click activity are immediately visible.
- **Time-spent view:** element overlays filled on a sequential orange ramp by
  accumulated visible time, so "where users spend the most time" reads at a
  glance; route-level dwell is summarized alongside.
- **Element overlays + tooltips:** hover any element rectangle for its
  track-id/selector, click count, and dwell time.
- **Screenshot underlay:** optionally load a screenshot of the screen being
  analyzed; the heatmap renders over it. Without one, a wireframe of the
  elements observed in the log is drawn instead.
- **Table view:** per-element numbers (clicks, dwell, share) as a sortable
  table — the accessible, exact counterpart to the color encoding.
- **Route selector + legend** with real min/max values; light and dark theme.

Visualization design follows a validated data-viz method: sequential encodings
are one hue light→dark (never a rainbow ramp), color never carries meaning
alone (tooltips + table view), text stays in ink tokens, and both themes are
explicitly designed rather than auto-inverted.

### 3.3 Recommended tools & libraries

| Concern | Recommendation | Why |
|---|---|---|
| Collector | **Vanilla JS** (provided) | Must run inside the product with zero deps and minimal bytes; nothing to vendor. |
| Log transport | `navigator.sendBeacon` / `fetch keepalive` | Survives unload; built into every browser. |
| Server append | Existing backend + `fs.appendFile` (or stack equivalent) | No new service. |
| Processing | **Python 3 stdlib** (provided) | Universally available offline; no package index needed. Optional: pandas/DuckDB *if* the analyst machine has them — the NDJSON loads directly. |
| Rendering | **Canvas 2D** with a palette LUT (provided) | Smooth density rendering with zero libraries; heatmap.js/d3 would add value only if we later need pan/zoom or comparative small multiples. |
| Screenshots | Any screenshot; stored outside the log | Keeps logs small and avoids shipping UI imagery through log pipelines. |

### 3.4 Sizing & performance envelope

- 1 000 daily active users × ~60 interactions ≈ 60 000 events/day ≈ 12 MB/day
  raw, ~1.5 MB gzipped — negligible next to pod logs.
- The browser aggregator handles ~500 k events in a few seconds; beyond that,
  Path B reduces any volume to a few hundred KB of aggregate JSON.

---

## 4. Rollout plan

1. **Week 1:** integrate collector behind a feature flag; add the ingestion
   endpoint + rotation; tag top-20 elements with `data-track-id`.
2. **Week 2:** enable on one pilot deployment; verify files appear in the log
   bundle; analysts validate the visualizer against known behavior.
3. **Then:** enable by default (respecting any tenant-level telemetry opt-out),
   document the analyst workflow, and iterate on which elements get track-ids
   based on what questions the first heatmaps raise.

## 5. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Layout differs across releases → overlays misalign with screenshots | Log carries `app` version; visualizer groups by route and shows the version range; analysts pair screenshots per version. |
| Log volume growth | Size-capped rotation, gzip, `sampleRate`, retention N days. |
| Selector churn on untagged elements | Prefer `data-track-id` for anything you intend to track over time; selectors are the fallback, not the plan. |
| Privacy review | Field whitelist in the spec; no free-text anywhere in the schema; query strings stripped. |

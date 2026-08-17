# Log Format Specification — `heatmap-events/1`

**Status:** Normative · **File family:** `heatmap-interactions-YYYY-MM-DD.ndjson[.gz]`

## 1. Container

- **Encoding:** UTF-8, one JSON object per line (NDJSON), `\n` terminated.
- **Ordering:** append order; consumers MUST NOT assume global timestamp order
  (client batching may interleave sessions).
- **Robustness:** consumers MUST skip lines that fail to parse or fail the
  schema gate (a crash may truncate the final line).
- **Compression:** rotated files MAY be gzipped; consumers SHOULD accept `.gz`.

## 2. Common envelope (every event)

| Field | Type | Req | Description |
|---|---|---|---|
| `v` | int | ✔ | Schema version. This spec defines `1`. Consumers skip unknown versions. |
| `t` | string | ✔ | Event type: `"click"` \| `"dwell"` \| `"view"` \| `"field"` \| `"form"`. Unknown types are skipped, enabling forward-compatible additions (e.g. `"scroll"`). |
| `ts` | int | ✔ | Event time, Unix epoch **milliseconds** (client clock). |
| `sid` | string | ✔ | Random per-browser-session UUID v4. Anonymous; never derived from user identity. |
| `route` | string | ✔ | Normalized route/screen identifier, e.g. `"/orders/:id"` if the router template is known, else the pathname. Query strings MUST be stripped. |
| `vp` | object | ✔ | Viewport at event time: `{"w": int, "h": int}` CSS pixels. |
| `app` | string | – | Application version string, for cohorting across releases. |

## 3. Event types

### 3.1 `click`

| Field | Type | Req | Description |
|---|---|---|---|
| `x`, `y` | float | ✔ | Click position normalized to the viewport, each in `[0,1]`, 4-decimal precision. |
| `el` | object | ✔ | Target element, see §4. |

```json
{"v":1,"t":"click","ts":1755446400123,"sid":"5f0c…","route":"/orders","vp":{"w":1440,"h":900},"x":0.8215,"y":0.0733,"el":{"tid":"orders.export","rect":[0.78,0.05,0.09,0.045]},"app":"2.14.0"}
```

### 3.2 `dwell`

Accumulated **visible** time. Two granularities share the type:

| Field | Type | Req | Description |
|---|---|---|---|
| `ms` | int | ✔ | Visible milliseconds accumulated since the last `dwell` flush for this scope. |
| `el` | object | – | Present ⇒ element-level dwell (element was ≥50 % visible). Absent ⇒ route-level dwell (tab visible on this route). |

Emitted on route change, `visibilitychange` → hidden, `pagehide`, and every 30 s
for long stays (so a lost final batch bounds the undercount at 30 s). Consumers
**sum** `ms` per scope.

```json
{"v":1,"t":"dwell","ts":1755446431000,"sid":"5f0c…","route":"/orders","vp":{"w":1440,"h":900},"ms":30000}
{"v":1,"t":"dwell","ts":1755446431000,"sid":"5f0c…","route":"/orders","vp":{"w":1440,"h":900},"ms":21540,"el":{"tid":"orders.table","rect":[0.03,0.18,0.94,0.72]}}
```

### 3.3 `view`

Route entered (initial load or SPA navigation). No extra fields. Serves as the
denominator for per-view rates.

```json
{"v":1,"t":"view","ts":1755446400001,"sid":"5f0c…","route":"/orders","vp":{"w":1440,"h":900},"app":"2.14.0"}
```

### 3.4 `field`

Engagement with one form field, emitted on blur (focus lost). Captures **how**
users fill forms without capturing **what** they type: the only value-derived
datum is the boolean `filled`.

| Field | Type | Req | Description |
|---|---|---|---|
| `ms` | int | ✔ | Milliseconds the field held focus during this focus episode. |
| `edits` | int | ✔ | Count of `input` events during the episode (typing/paste/toggle activity — never content). |
| `filled` | bool | ✔ | Field non-empty (text), checked (checkbox/radio), or selected (select) at blur. Derived in-collector; the value itself is never read out. |
| `el` | object | ✔ | The field, see §4 (`name` allowed here). |
| `form` | string | – | Identity of the enclosing `<form>` (same precedence as §4). Absent for free-standing fields. |
| `ftype` | string | – | Input type (`"text"`, `"email"`, `"checkbox"`, `"select"`, …). |

A field focused several times produces several events; consumers **sum** `ms`
and `edits` and **OR** `filled` per field.

```json
{"v":1,"t":"field","ts":1755446500123,"sid":"5f0c…","route":"/signup","vp":{"w":1440,"h":900},"ms":8210,"edits":14,"filled":true,"ftype":"email","el":{"name":"email","rect":[0.35,0.30,0.30,0.05]},"form":"signup"}
```

### 3.5 `form`

Outcome of one form attempt, emitted on submit, or on route change / page hide
while touched fields remain (abandonment).

| Field | Type | Req | Description |
|---|---|---|---|
| `form` | string | ✔ | Form identity. |
| `outcome` | string | ✔ | `"submit"` \| `"abandon"`. |
| `touched` | int | ✔ | Distinct fields the user focused during the attempt. |
| `filled` | int | ✔ | Distinct fields left filled at attempt end. |
| `ms` | int | ✔ | Total focused milliseconds across the attempt's fields. |
| `fields` | int | – | Total fields the form contains, if known (funnel denominator). |

```json
{"v":1,"t":"form","ts":1755446550000,"sid":"5f0c…","route":"/signup","vp":{"w":1440,"h":900},"form":"signup","outcome":"abandon","touched":3,"filled":2,"ms":21300,"fields":5}
```

## 4. Element object (`el`)

| Field | Type | Req | Description |
|---|---|---|---|
| `tid` | string | –* | `data-track-id` value. Preferred stable identity. |
| `id` | string | –* | Element `id` attribute (only if not framework-generated). |
| `name` | string | –* | `name` attribute — form fields and forms only. |
| `sel` | string | –* | Generated structural CSS selector, ≤ 4 levels, e.g. `"main>div.toolbar>button:nth-of-type(2)"`. |
| `rect` | array | ✔ | Bounding box normalized to viewport: `[x, y, w, h]`, floats in `[0,1]`, 4-decimal precision. |

*At least one of `tid` / `id` / `name` / `sel` MUST be present. Consumers key
elements by the first present of `tid` → `id` → `name` → `sel`.

**Privacy invariant:** no field in this schema may carry element text content,
input values, attribute values other than `id`/`data-track-id`, or any
user-identifying data. Additions to the schema must preserve this invariant.

## 5. Aggregate format — `heatmap-aggregate/1` (informative)

Output of `processor/process_heatmap_logs.py`; also accepted by the visualizer.

```json
{
  "schema": "heatmap-aggregate/1",
  "generatedAt": "2026-08-17T12:00:00Z",
  "source": {"files": 3, "events": 61230, "skipped": 4,
             "from": 1754006400000, "to": 1755446400000},
  "grid": {"cols": 48, "rows": 27},
  "routes": {
    "/orders": {
      "views": 812, "sessions": 341, "clicks": 5120,
      "dwellMs": 9834000,
      "vp": {"w": 1440, "h": 900},
      "clickGrid": [[0,0,3, …], …],
      "elements": {
        "orders.export": {"clicks": 214, "dwellMs": 0,
                           "rect": [0.78,0.05,0.09,0.045], "kind": "tid"}
      }
    }
  }
}
```

- `clickGrid` is `rows` arrays of `cols` integers (row-major, top-left origin)
  counting clicks per normalized-viewport cell.
- `elements` is keyed by resolved element identity (§4); `rect` is the
  event-count-weighted mean box; `kind` records which identity field keyed it.
- `vp` is the most common viewport for the route (used to pick the render
  aspect ratio).

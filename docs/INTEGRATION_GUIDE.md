# Integration Guide — instrumenting an existing app

How to wire an existing application (example used throughout: a **login page**,
a **dashboard with cards**, **modals**, and **forms**) so user interactions land
in a text file you can drop into this repo's visualizer.

Total UI changes: **one script tag + one init call**. Everything else in this
guide is optional polish.

## Step 1 — Add the collector

Copy `collector/heatmap-collector.js` into your project's static assets and
include it on every page (app shell, base template, or `index.html`):

```html
<script src="/assets/heatmap-collector.js"></script>
<script>
  HeatmapCollector.init({
    endpoint: "http://localhost:8477/api/v1/ux-events",  // see step 2
    app: "1.0.0"                                          // your app version
  });
</script>
```

For a multi-page app (separate login page and dashboard page), include it on
both pages — the session ID survives navigation within the browser session.
For an SPA, one include in the shell is enough; route changes are hooked
automatically via the History API.

That's it — clicks, time-on-screen, and form engagement are now recorded for
every existing element, using the `id`/`name` attributes your UI already has.
Nothing typed into any field (username, password, form inputs) is ever
captured; only focus time, edit counts, and filled-or-not booleans.

## Step 2 — Choose where the file gets written

The collector POSTs JSON batches; something must append them to a file. Two
options:

**Option A — the bundled log server (no backend changes at all).** Run this
repo's zero-dependency server next to your app:

```bash
node server/log-server.js --port 8477 --out ./heatmap-interactions.ndjson
```

Point `endpoint` at `http://localhost:8477/api/v1/ux-events` (or the host's
LAN address if the app runs on other machines in the private network). The
output file is the finished artifact — a plain text file, one JSON object per
line.

**Option B — your own backend.** Add one append-only route (~15 lines; full
snippet in `PROPOSAL.md` §2.3) that writes the same NDJSON lines next to your
other logs, so the file rides your existing log download.

## Step 3 (optional) — Name the things you care about

Everything already works via generated selectors, but stable, readable names
make the heatmap tables much nicer. Add `data-track-id` to the elements that
matter:

```html
<!-- login page -->
<form id="login">                                  <!-- id works as-is -->
  <input name="username" type="text">              <!-- name works as-is -->
  <input name="password" type="password">
  <button data-track-id="login.submit">Sign in</button>
</form>

<!-- dashboard cards -->
<div class="card" data-track-id="dash.card.revenue">…</div>
<div class="card" data-track-id="dash.card.alerts">…</div>
<div class="card" data-track-id="dash.card.activity">…</div>

<!-- a modal and its actions -->
<div class="modal" data-track-id="modal.export">
  …
  <button data-track-id="modal.export.confirm">Export</button>
  <button data-track-id="modal.export.cancel">Cancel</button>
</div>
```

Two bonuses from tagging:

- Tagged elements also get **visible-time tracking** (IntersectionObserver),
  so dashboard cards show up in the *Time spent* view even without clicks.
- Clicks on icons/labels inside a tagged element are attributed to the tagged
  ancestor, keeping counts consolidated.

If your app renders content dynamically (cards added after load, modals
mounted on open), call `HeatmapCollector.rescan()` after big DOM updates so
new `data-track-id` elements are observed. SPA route changes trigger a rescan
automatically.

## Step 4 (optional) — Modals as virtual screens

Clicks inside a modal are already attributed to the modal's tagged elements.
But the *pixel* heatmap mixes modal-open and modal-closed states, since both
happen on the same route. If a modal is big enough that you want its own
heatmap screen, report it as a virtual route:

```js
let openModal = null;   // set/cleared by your modal open/close code

HeatmapCollector.init({
  endpoint: "…",
  route: () => location.pathname + (openModal ? "::" + openModal : "")
});
```

Now `/dashboard` and `/dashboard::modal.export` appear as separate screens in
the visualizer, each with its own clean heatmap. (The `route` callback is also
the place to return router *templates* like `/orders/:id` so detail pages
aggregate into one screen instead of one per ID.)

## Step 5 — Visualize

1. Take the text file (`heatmap-interactions.ndjson` from option A, or the
   downloaded log from option B).
2. Open `visualizer/index.html` from this repo in any browser.
3. Drop the file in. Pick the screen, switch **Clicks** / **Time spent**,
   and check the form tables for fill rates and abandonment.
4. Optional: screenshot your login/dashboard screens (browser F12 →
   device toolbar → capture, or any tool) and load one as the underlay so the
   heatmap sits on your real UI.

Large collections (many days, many machines): pre-aggregate first —
`python3 processor/process_heatmap_logs.py *.ndjson -o agg.json` — and drop
the aggregate in instead.

## Checklist

- [ ] `heatmap-collector.js` copied into the project and included on every page
- [ ] `HeatmapCollector.init({ endpoint })` called once per page load
- [ ] Log destination running (bundled `server/log-server.js` or your backend route)
- [ ] (nice-to-have) `data-track-id` on cards, modals, and primary buttons
- [ ] (nice-to-have) virtual routes for full-screen modals / router templates
- [ ] File drops into `visualizer/index.html` and screens appear

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| No file appears | Check the browser console for blocked POSTs; verify the endpoint URL and that the log server is reachable from the app's origin (it sends permissive CORS headers). |
| Events appear only after ~10 s | Normal — the collector batches (10 s / 50 events). `HeatmapCollector.flush()` forces it. |
| Cards missing from Time spent view | They're untagged. Add `data-track-id` (visible-time tracking only observes tagged elements), or rely on clicks. |
| Modal clicks blur into the page heatmap | Use a virtual route (step 4). |
| One detail page per ID in the screen list | Return the router template from the `route` callback (step 4). |

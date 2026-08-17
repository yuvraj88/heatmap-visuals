# Offline User Interaction Heatmap

Heatmap analytics for applications running in **restricted / air-gapped networks**
(factories, malls, private clouds) where SaaS tracking tools (Hotjar, FullStory,
Clarity…) cannot be used.

The pipeline has three completely decoupled stages, connected only by log files:

```
┌─────────────────────────────┐      ┌──────────────────────┐      ┌───────────────────────────┐
│ 1. COLLECT (inside network) │      │ 2. DOWNLOAD          │      │ 3. VISUALIZE (your laptop)│
│ collector/heatmap-          │ ───► │ heatmap-interactions │ ───► │ visualizer/index.html     │
│ collector.js  → app backend │      │ -*.ndjson joins the  │      │ (open the file, drop the  │
│ appends NDJSON log lines    │      │ existing log bundle  │      │  log in, see the heatmap) │
└─────────────────────────────┘      └──────────────────────┘      └───────────────────────────┘
```

No stage needs internet access. No stage needs a database. The interaction data
rides the **log download mechanism you already have**.

## Repository layout

| Path | What it is |
|---|---|
| `docs/PROPOSAL.md` | The full proposal: collection strategy, log integration, processing, visualization |
| `docs/LOG_FORMAT_SPEC.md` | Normative spec of the `heatmap-events/1` NDJSON log format |
| `collector/heatmap-collector.js` | Zero-dependency browser SDK that captures clicks + time-spent and ships them to your backend logger |
| `processor/process_heatmap_logs.py` | Python 3 (stdlib only) aggregator: NDJSON logs → compact `heatmap-aggregate/1` JSON |
| `visualizer/index.html` | Self-contained offline viewer (no CDN, no build step, works from `file://`) |
| `examples/generate_sample.py` | Generates realistic sample logs for demo/testing |
| `examples/sample-heatmap.ndjson` | Pre-generated sample log — drop it into the visualizer to try it out |

## Quick start (try it in 30 seconds)

1. Open `visualizer/index.html` in any modern browser (double-click works — it
   runs from `file://`).
2. Drag `examples/sample-heatmap.ndjson` onto the drop zone.
3. Pick a route, switch between **Clicks** and **Time spent**, hover the element
   overlays, and check the table view.

Optionally upload a screenshot of the corresponding app screen to overlay the
heatmap on the real UI; without one, the visualizer draws a wireframe of the
elements it saw in the log.

## Production flow

1. **Instrument** the app: include `collector/heatmap-collector.js` and call
   `HeatmapCollector.init({ endpoint: "/api/v1/ux-events" })`. Tag important
   elements with `data-track-id="..."` (untagged elements still work via
   generated CSS selectors).
2. **Log**: the endpoint appends each received event as one NDJSON line to
   `heatmap-interactions-YYYY-MM-DD.ndjson` next to your other logs (see
   `docs/PROPOSAL.md` §2 for the ~15-line handler).
3. **Download** the log bundle as usual.
4. **Visualize**: drop the `.ndjson` file(s) straight into the visualizer, or
   pre-aggregate first when files are large:

   ```bash
   python3 processor/process_heatmap_logs.py logs/heatmap-interactions-*.ndjson \
       -o heatmap-aggregate.json
   ```

   and drop the resulting `heatmap-aggregate.json` in instead. Both formats are
   accepted.

## Privacy

The collector records **where** users interact, never **what** they type or read:
coordinates, element selectors, and durations only. Session IDs are random per
browser session. See `docs/PROPOSAL.md` §1.4.

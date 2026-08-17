#!/usr/bin/env node
/**
 * Standalone heatmap log server — zero dependencies.
 *
 * Accepts collector batches (POST /api/v1/ux-events) and appends each event as
 * one NDJSON line to a plain text file. Use this when the app you're
 * instrumenting has no convenient backend to extend, or for local development:
 *
 *     node server/log-server.js --port 8477 --out ./heatmap-interactions.ndjson
 *
 * then point the collector at it:
 *
 *     HeatmapCollector.init({ endpoint: "http://localhost:8477/api/v1/ux-events" });
 *
 * CORS is wide open (dev tool, private network). The output file is the exact
 * file the visualizer and processor consume — no conversion step.
 */
"use strict";

const http = require("http");
const fs = require("fs");
const path = require("path");

const args = process.argv.slice(2);
function argOf(flag, dflt) {
  const i = args.indexOf(flag);
  return i !== -1 && args[i + 1] ? args[i + 1] : dflt;
}
const PORT = parseInt(argOf("--port", "8477"), 10);
const OUT = path.resolve(argOf("--out", "./heatmap-interactions.ndjson"));
const MAX_BODY = 1024 * 1024; // 1 MB per batch

const VALID_TYPES = new Set(["click", "dwell", "view", "field", "form"]);
let written = 0;

const server = http.createServer((req, res) => {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");

  if (req.method === "OPTIONS") { res.writeHead(204).end(); return; }

  if (req.method === "GET" && req.url === "/health") {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ ok: true, out: OUT, written }));
    return;
  }

  if (req.method !== "POST" || !req.url.startsWith("/api/v1/ux-events")) {
    res.writeHead(404).end();
    return;
  }

  let body = "";
  let overflow = false;
  req.on("data", chunk => {
    body += chunk;
    if (body.length > MAX_BODY) { overflow = true; req.destroy(); }
  });
  req.on("end", () => {
    if (overflow) return;
    let events = [];
    try {
      const parsed = JSON.parse(body);
      events = Array.isArray(parsed) ? parsed
             : Array.isArray(parsed.events) ? parsed.events : [];
    } catch (e) { /* ignore malformed batch */ }
    const lines = events
      .filter(e => e && e.v === 1 && VALID_TYPES.has(e.t))
      .map(e => JSON.stringify(e) + "\n")
      .join("");
    if (lines) {
      fs.appendFile(OUT, lines, err => {
        if (err) console.error("append failed:", err.message);
        else {
          written += lines.split("\n").length - 1;
          process.stdout.write(`\r${written} events -> ${OUT}   `);
        }
      });
    }
    res.writeHead(204).end();   // fire-and-forget: never block the UI
  });
});

server.listen(PORT, () => {
  console.log(`heatmap log server listening on http://localhost:${PORT}`);
  console.log(`POST /api/v1/ux-events  ->  ${OUT}`);
});

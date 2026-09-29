// Spike SP-1 (Sprint 3): can the canvas handle 500 nodes / 1,000 edges at an acceptable frame rate?
// Usage: API on :8000 and web on :5173 running, then `node perf/sp1-canvas-scale.mjs`
import { chromium } from "@playwright/test";

const API = "http://localhost:8000/v1";
const N = 500;
const COLS = 25;
const nodes = [], edges = [], layout = {};
for (let i = 0; i < N; i++) {
  nodes.push({ id: `n_${i}`, type: "core.set", name: `step_${i}`, config: { values: { a: `v${i}` } } });
  layout[`n_${i}`] = { x: (i % COLS) * 280, y: Math.floor(i / COLS) * 160 };
}
edges.push({ id: "e_start", kind: "flow", source: { node: "START" }, target: { node: "n_0" } });
for (let i = 0; i < N - 1; i++) edges.push({ id: `e_${i}`, kind: "flow", source: { node: `n_${i}` }, target: { node: `n_${i + 1}` } });
for (let i = 0; i + COLS < N && edges.length < 1000; i++) {
  edges.push({ id: `x_${i}`, kind: "flow", source: { node: `n_${i}` }, target: { node: `n_${i + COLS}` } });
}
edges.push({ id: "e_end", kind: "flow", source: { node: `n_${N - 1}` }, target: { node: "END" } });
const ir = { name: "SP-1 scale", state: { channels: [{ name: "a", reducer: "append", type: { type: "array" } }] }, nodes, edges };

const json = (r) => r.json();
const wf = await fetch(`${API}/projects/prj_default/workflows`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ name: "SP-1 scale" }) }).then(json);
let t = performance.now();
const saved = await fetch(`${API}/workflows/${wf.id}`, { method: "PUT", headers: { "content-type": "application/json", "If-Match": "1" }, body: JSON.stringify({ ir: { ...ir, id: wf.id } }) }).then(json);
const saveMs = performance.now() - t;
await fetch(`${API}/workflows/${wf.id}/layout`, { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ positions: layout }) });
t = performance.now();
const cmd = await fetch(`${API}/workflows/${wf.id}/commands`, { method: "POST", headers: { "content-type": "application/json", "If-Match": String(saved.version) }, body: JSON.stringify({ commands: [{ op: "UpdateNode", id: "n_10", patch: { label: "edited" } }] }) }).then(json);
const commandMs = performance.now() - t;

const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM_PATH });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
t = performance.now();
await page.goto(`http://localhost:5173/workflows/${wf.id}`);
await page.getByTestId("node-step_499").waitFor({ state: "attached", timeout: 60000 });
const loadMs = performance.now() - t;
const pane = page.locator(".react-flow__pane");
const box = await pane.boundingBox();
const fps = async (zoomIn) => {
  if (zoomIn) { for (let i = 0; i < 6; i++) await page.getByRole("button", { name: "Zoom In" }).click(); }
  await page.evaluate(() => { window.__frames = 0; const tick = () => { window.__frames++; window.__raf = requestAnimationFrame(tick); }; tick(); });
  await page.mouse.move(box.x + 300, box.y + 300);
  await page.mouse.down();
  const t0 = Date.now();
  for (let i = 0; i < 120; i++) await page.mouse.move(box.x + 300 + (i % 60) * 5, box.y + 300 + (i % 40) * 3);
  await page.mouse.up();
  const secs = (Date.now() - t0) / 1000;
  return (await page.evaluate(() => { cancelAnimationFrame(window.__raf); return window.__frames; })) / secs;
};
const fpsOverview = await fps(false);
const fpsZoomed = await fps(true);
console.log(JSON.stringify({
  nodes: N, edges: edges.length, diagnostics: saved.diagnostics.length,
  save_ms: Math.round(saveMs), command_ms: Math.round(commandMs), editor_load_ms: Math.round(loadMs),
  pan_fps_overview: Math.round(fpsOverview), pan_fps_zoomed: Math.round(fpsZoomed), command_version: cmd.version,
}, null, 2));
await browser.close();

#!/usr/bin/env node
// Screenshots the page, or its house map in a view, with headless Chromium, to check a change by eye. A running
// server's page, as the browser shows it, and any errors in its console.
//
//   node tools/shot.mjs [options] [out.png]   # out.png in the temp folder, if not given
//     --url URL         the page (default http://127.0.0.1:8000/)
//     --view NAME       the map's view: plan, 3d or detailed (default: the page as it loads, all of it)
//     --level NAME      in the detailed view, cut away to a level by its floor's name, as Upper or Main; or outside
//     --no-trees        in the detailed view, the whole house with its trees hidden
//     --season NAME     or with its trees in a season: spring, summer, fall or winter (default: today's)
//     --angle DEG       turn the 3D or detailed view to an angle, 0 looking at the front
//     --tilt DEG        and tilt it, 90 looking straight down
//     --top             straight down, the front at the bottom (--angle 0 --tilt 90)
//     --room NAME       zoom the detailed view in on a room or area, or anything else in it by name, as Fireplace
//     --zoom N          how far, 1 the whole lot (default 3 with --room)
//     --full            the map full screen, the browser's own (with --fallback, fixed over the page instead)
//     --width PX        the window's width (default 1100), under 500 as a phone
//     --height PX       the window's height (default 800 full screen, else 3000, for the whole page)
//     --dark            in dark mode
//
// It needs Chromium: $CHROME, else chromium, google-chrome or Playwright's from the PATH or its cache.
import { spawn, execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import { parseArgs } from "node:util";

const { values: opt, positionals } = parseArgs({
  allowPositionals: true,
  options: {
    url: { type: "string", default: "http://127.0.0.1:8000/" },
    view: { type: "string" },
    level: { type: "string" },
    "no-trees": { type: "boolean" },
    season: { type: "string" },
    angle: { type: "string" },
    tilt: { type: "string" },
    top: { type: "boolean" },
    room: { type: "string" },
    zoom: { type: "string" },
    full: { type: "boolean" },
    fallback: { type: "boolean" },
    width: { type: "string", default: "1100" },
    height: { type: "string" },
    dark: { type: "boolean" },
    help: { type: "boolean" },
  },
});
if (opt.help) {
  const { readFileSync } = await import("node:fs");
  console.log(readFileSync(new URL(import.meta.url), "utf8").split("\n").slice(1).filter((l) => l.startsWith("//")).map((l) => l.slice(3)).join("\n"));
  process.exit(0);
}
const out = positionals[0] ?? join(tmpdir(), "housemon-shot.png");

function chromium() {
  if (process.env.CHROME) return process.env.CHROME;
  for (const name of ["chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "headless_shell"]) {
    try {
      return execFileSync("which", [name], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
    } catch {}
  }
  const cache = join(homedir(), ".cache", "ms-playwright");
  for (const dir of existsSync(cache) ? readdirSync(cache).sort().reverse() : []) {
    for (const bin of ["chrome-linux/headless_shell", "chrome-linux/chrome", "chrome-headless-shell-linux64/chrome-headless-shell"]) {
      if (existsSync(join(cache, dir, bin))) return join(cache, dir, bin);
    }
  }
  throw new Error("No Chromium found: set CHROME to one");
}

const profile = mkdtempSync(join(tmpdir(), "housemon-shot-"));
const browser = spawn(chromium(), ["--headless", "--remote-debugging-port=0", "--enable-unsafe-swiftshader", "--use-angle=swiftshader", `--user-data-dir=${profile}`, "--no-first-run", "about:blank"], { stdio: ["ignore", "ignore", "pipe"] });
try {
  const wsUrl = await new Promise((resolve, reject) => {
    browser.stderr.on("data", (d) => { const m = String(d).match(/ws:\/\/\S+/); if (m) resolve(m[0]); });
    browser.on("exit", () => reject(new Error("Chromium quit")));
  });
  const ws = new WebSocket(wsUrl);
  await new Promise((resolve) => (ws.onopen = resolve));
  let id = 0;
  const pending = new Map();
  const events = [];
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); } else events.push(msg);
  };
  const send = (method, params = {}, sessionId) => new Promise((resolve) => { const i = ++id; pending.set(i, resolve); ws.send(JSON.stringify({ id: i, method, params, sessionId })); });
  const { result: { targetId } } = await send("Target.createTarget", { url: "about:blank" });
  const { result: { sessionId } } = await send("Target.attachToTarget", { targetId, flatten: true });
  const page = (method, params) => send(method, params, sessionId);
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const run = async (js, userGesture = false) => {
    const r = await page("Runtime.evaluate", { expression: `(async () => { ${js} })()`, awaitPromise: true, returnByValue: true, userGesture });
    if (r.result?.exceptionDetails) throw new Error(r.result.exceptionDetails.exception?.description ?? r.result.exceptionDetails.text);
    return r.result?.result?.value;
  };
  await page("Runtime.enable");
  await page("Page.enable");
  const width = Number(opt.width);
  const height = Number(opt.height ?? (opt.full ? 800 : 3000));
  await page("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 2, mobile: width < 500 });
  await page("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: opt.dark ? "dark" : "light" }] });
  await page("Page.navigate", { url: opt.url });
  await wait(2500);

  const view = { plan: "Floor plan", "3d": "3D", detailed: "Detailed" }[opt.view?.toLowerCase()];
  if (opt.view && !view) throw new Error(`--view is plan, 3d or detailed, not ${opt.view}`);
  const steps = [];
  const click = (selector, text) => `{ const b = [...document.querySelectorAll(${JSON.stringify(selector)})].find((b) => b.textContent === ${JSON.stringify(text)}); if (!b) throw new Error("No button " + ${JSON.stringify(text)}); b.click(); await new Promise((r) => setTimeout(r, 700)); }`;
  if (view) steps.push(click(".map figcaption button", view));
  if (opt.level) steps.push(click(".cuts button", /^outside$/i.test(opt.level) ? "Outside" : `${opt.level} level`));
  if (opt.season) steps.push(click(".cuts .seasons button", opt.season[0].toUpperCase() + opt.season.slice(1).toLowerCase()));
  if (opt["no-trees"]) {
    steps.push(`{ const box = document.querySelector(".cuts .trees input"); if (!box || box.closest("[hidden]")) throw new Error("No trees to hide"); if (box.checked) box.click(); await new Promise((r) => setTimeout(r, 700)); }`);
  }
  const [angle, tilt] = opt.top ? [0, 90] : [opt.angle, opt.tilt];
  if (angle != null || tilt != null) {
    steps.push(`moveMap(${angle ?? "state.mapAngle"} - state.mapAngle, ${tilt ?? "state.mapTilt"} - state.mapTilt); await new Promise((r) => setTimeout(r, 400));`);
  }
  if (opt.room) {
    steps.push(`{ const item = state.modelScene?.items.find((i) => i.name === ${JSON.stringify(opt.room)}); if (!item) throw new Error("Nothing named " + ${JSON.stringify(opt.room)} + " in the detailed view"); zoomModel(${Number(opt.zoom ?? 3)}, state.map.layout.centerOf(item.key)); await new Promise((r) => setTimeout(r, 700)); }`);
  } else if (opt.zoom) {
    steps.push(`zoomModel(${Number(opt.zoom)}); await new Promise((r) => setTimeout(r, 700));`);
  }
  for (const step of steps) await run(step);
  if (opt.full) {
    // As a click would: the browser's full screen, which needs one, or without it the fallback, over the page.
    await run(`${opt.fallback ? "Object.defineProperty(document, \"fullscreenEnabled\", { value: false }); " : ""}state.map.full.click(); await new Promise((r) => setTimeout(r, 900));`, !opt.fallback);
  }
  await wait(300);

  const rect = opt.full
    ? { x: 0, y: 0, width, height }
    : await run(`const r = document.querySelector(${JSON.stringify(view ? "#map" : "body")}).getBoundingClientRect(); return { x: r.x + scrollX, y: r.y + scrollY, width: r.width, height: r.height };`);
  const pad = view && !opt.full ? 8 : 0;
  const shot = await page("Page.captureScreenshot", { format: "png", clip: { x: Math.max(0, rect.x - pad), y: Math.max(0, rect.y - pad), width: rect.width + 2 * pad, height: rect.height + 2 * pad, scale: 1 } });
  writeFileSync(out, Buffer.from(shot.result.data, "base64"));
  const errors = events.flatMap((msg) => {
    if (msg.method === "Runtime.exceptionThrown") return [msg.params.exceptionDetails.exception?.description ?? msg.params.exceptionDetails.text];
    if (msg.method === "Runtime.consoleAPICalled" && ["error", "warning"].includes(msg.params.type)) return [msg.params.args.map((a) => a.value ?? a.description).join(" ")];
    return [];
  });
  console.log(out, errors.length ? `with errors:\n${errors.join("\n")}` : "(no console errors)");
  ws.close();
} finally {
  const exited = browser.exitCode != null || new Promise((resolve) => browser.once("exit", resolve));
  browser.kill();
  await exited;
  rmSync(profile, { recursive: true, force: true, maxRetries: 5 });
}

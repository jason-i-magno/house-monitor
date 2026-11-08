// The map's detailed view outside a browser: the house as the server sends it, and the scene model.js builds of
// it, for tools that check the drawing (scan.mjs). Node 22 or later, and python3, both from the repo's root.
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const root = new URL("..", import.meta.url);

// model.js's functions, run in a context of their own: it needs nothing of the page to build a scene or its meshes.
export function loadModel() {
  const context = vm.createContext({});
  const code = readFileSync(new URL("housemon/web/model.js", root), "utf8");
  vm.runInContext(`${code}\n;globalThis.exported = { modelScene, buildModel, modelShows, MODEL_TOKENS, MODEL_CUT };`, context);
  return context.exported;
}

// The house as /api/sensors sends it, without readings: the house file given, else as the server picks it.
export function loadHouse(house) {
  const args = ["-m", "housemon", "--dump", ...(house ? ["--house", house] : [])];
  return JSON.parse(execFileSync("python3", args, { cwd: root, encoding: "utf8", maxBuffer: 64 << 20 }));
}

// The light theme's model colors from style.css, but where the house has its own, as the page picks them.
export function themeColors(tokens) {
  const css = readFileSync(new URL("housemon/web/style.css", root), "utf8");
  const hex = Object.fromEntries(tokens.map((token) => [token, css.match(new RegExp(`--model-${token}:\\s*(#[0-9a-fA-F]{6})`))?.[1] ?? "#808080"]));
  return (details) => Object.fromEntries(tokens.map((token) => {
    const color = details.colors?.[token]?.[0] ?? hex[token];
    return [token, [1, 3, 5].map((i) => parseInt(color.slice(i, i + 2), 16) / 255)];
  }));
}

// The scene of a house, as the page builds it.
export function sceneOf(model, house) {
  return model.modelScene({
    rooms: house.sensors.filter((s) => !s.outside),
    areas: house.areas,
    details: house.details,
    wall: house.wall,
    colors: themeColors(model.MODEL_TOKENS),
  });
}

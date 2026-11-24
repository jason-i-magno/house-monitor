// The garden planner: each of the house's planted beds (its details' grounds of the kind "bed", in soil or mulch) as
// a grid of square feet, planted from plants.json a square at a time. It says what each plant needs, which neighbors
// help or hinder it, how much sun each square gets (the shade the house, trees and fences cast, from the detailed
// view's scene), what water reaches the bed from the sprinklers, and when to start, plant and harvest, from the house
// file's GARDEN (garden.py). What's planted is saved on the server (api/garden), a bed at a time, or where the
// server keeps no plans, as in the cloud service, in this browser.

/**
 * A plant, as plants.json has it: how many fit in a square foot (perSquare), or for a big one, how many squares it
 * takes on a side (size); its height in inches; the hours of direct sun it needs at least and does best with; when to
 * start it indoors, plant it out (weeks from the last frost) and sow it for fall (weeks from the first); the days from
 * then to its first harvest, and the weeks that lasts.
 * @typedef {{
 *   id: string, name: string, kind: "vegetable" | "herb" | "fruit" | "flower", family: string, food: boolean,
 *   perennial: boolean, perSquare: number, size: number, height: number, sun: [number, number],
 *   water: "low" | "medium" | "high", season: "cool" | "warm", frost: "tender" | "light" | "hard",
 *   start: [number, number] | null, plant: [number, number] | null, fall: [number, number] | null,
 *   how: "seed" | "transplant" | "either" | "set", days: [number, number], harvest: number, trellis: boolean,
 *   companions: string[], avoid: string[], tips: string,
 * }} Plant
 */
/** @typedef {{plant: string, x: number, y: number}} Planting a plant in a bed, at its back-left square */
/**
 * A bed to plan: its name, the ground it is, its squares across (w, along x) and deep (d, along y), and whether
 * it's soil, as a vegetable bed is, rather than mulch.
 * @typedef {{name: string, ground: Ground, w: number, d: number, soil: boolean}} Bed
 */
/** @typedef {[Date, Date]} Span from one day to another */
/**
 * When a plant is started, planted and harvested in a season: each a span, or null where it isn't, and its harvests,
 * one after each planting.
 * @typedef {{start: Span | null, plant: Span | null, fall: Span | null, harvest: Span[]}} Windows
 */
/**
 * A note on a bed: a warning, a good pairing, or a tip, and the plantings it's about, by index.
 * @typedef {{level: "warn" | "good" | "tip", text: string, at: number[]}} GardenNote
 */
/**
 * The planner: its elements; the plants, by id and in order; each bed's plantings, by name, and where they're saved;
 * the bed shown; the brush (a plant's id, "remove", or null), the planting selected and one being moved, by index;
 * the keyboard's square; the palette's filters; each bed's undo steps; the sun worked out for each bed, each month
 * April to October a list of hours a square, and the month shown, and each season's scene for rays to be cast in;
 * the scenes the water's worked out from (both for
 * the house as it was then, its details and garden as JSON); the pointer's drag; the calendar's row in the readout;
 * and saves waiting to go.
 * @typedef {{
 *   root: HTMLElement, beds: HTMLElement, note: HTMLElement, tools: HTMLElement, plot: HTMLElement,
 *   svg: SVGSVGElement, key: HTMLElement, status: HTMLElement, search: HTMLInputElement, kinds: HTMLElement,
 *   soon: HTMLButtonElement, list: HTMLElement, card: HTMLElement, notes: HTMLElement, water: HTMLElement,
 *   calendar: HTMLElement, calendarSvg: SVGSVGElement, readout: HTMLElement, upcoming: HTMLElement,
 *   table: HTMLElement, sunButton: HTMLButtonElement, months: HTMLElement, removeButton: HTMLButtonElement,
 *   undoButton: HTMLButtonElement, clearButton: HTMLButtonElement, compass: HTMLElement,
 *   plants: Map<string, Plant>, catalog: Plant[], plans: Record<string, Planting[]>, store: "server" | "browser" | null,
 *   bed: string | null, brush: string | null, selected: number | null, moving: number | null,
 *   cursor: [number, number], kind: string, onlySoon: boolean, undo: Record<string, Planting[][]>,
 *   sun: Map<string, Float32Array[] | "working">, sunFor: string | null, sunMonth: number, showSun: boolean,
 *   casters: Map<Season, (o: number[], dir: number[]) => boolean>,
 *   scenes: Map<string, Scene | NoScene>, scenesFor: string | null, row: number | null,
 *   drag: {x: number, y: number, at: number, dx: number, dy: number, moved: boolean, paint: string | null,
 *     painted: Set<string>, changed: boolean, before: Planting[]} | null,
 *   ghost: {x: number, y: number, size: number} | null, saving: Map<string, number>, error: string | null,
 *   highlight: number[],
 * }} Planner
 */

const GARDEN_SAVE_KEY = "house-monitor-garden"; // the plans, in this browser, where the server keeps none
const GARDEN_BED_KEY = "house-monitor-garden-bed"; // the bed this viewer last looked at
const GARDEN_CELL = [30, 60]; // px a square foot takes on the page, least and most
const GARDEN_MONTHS = [3, 4, 5, 6, 7, 8, 9]; // the months the sun is worked out for, April to October
const GARDEN_KINDS = [["", "All"], ["vegetable", "Vegetables"], ["herb", "Herbs"], ["fruit", "Fruit"], ["flower", "Flowers"]];
const GARDEN_WATER = { low: 0.5, medium: 1, high: 1.5 }; // inches a week each kind of plant wants
const GARDEN_FROST_END = { tender: 0, light: 14, hard: 42 }; // days past the first frost each picks on
const GARDEN_SUN_BINS = [4, 6, 8]; // the sun's key: under 4 h, 4 to 6, 6 to 8, 8 or more
const GARDEN_SUN_KEY = ["Under 4 h", "4–6 h", "6–8 h", "8 h or more"];
const GARDEN_SOON = 6; // weeks ahead "Plant soon" looks
const GARDEN_STEP = 0.25; // hours between the sun's positions as it's worked out
const GARDEN_UNDO = 50;
const DAY = 86400000;
const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// --- The planner's elements

function createPlanner() {
  const beds = el("div", { class: "segmented garden-beds", role: "group", "aria-label": "The bed to plan" });
  const note = el("p", { class: "garden-note", hidden: "" });
  const sunButton = /** @type {HTMLButtonElement} */ (el("button", { type: "button", class: "toggle", "aria-pressed": "false" }, "Sun"));
  const months = el("div", { class: "segmented", role: "group", "aria-label": "The month the sun is shown for" });
  const removeButton = /** @type {HTMLButtonElement} */ (el("button", { type: "button", class: "toggle", "aria-pressed": "false", title: "Click or drag over plants to take them out" }, "Remove"));
  const undoButton = /** @type {HTMLButtonElement} */ (el("button", { type: "button" }, "Undo"));
  const clearButton = /** @type {HTMLButtonElement} */ (el("button", { type: "button" }, "Clear bed"));
  const compassNode = el("span", { class: "garden-compass", title: "North" });
  const tools = el("div", { class: "garden-tools" },
    el("div", { class: "garden-tool-group" }, sunButton, months),
    el("div", { class: "garden-tool-group" }, removeButton, undoButton, clearButton, compassNode));
  const svgNode = svg("svg", { class: "garden-bed", tabindex: 0, role: "grid", "aria-label": "The bed" });
  const plot = el("div", { class: "garden-plot" });
  plot.append(svgNode);
  const key = el("div", { class: "garden-key" });
  const status = el("p", { class: "garden-status", role: "status", "aria-live": "polite" });
  const search = /** @type {HTMLInputElement} */ (el("input", { type: "search", placeholder: "Find a plant", "aria-label": "Find a plant" }));
  const kinds = el("div", { class: "segmented", role: "group", "aria-label": "Kinds of plants" });
  const soon = /** @type {HTMLButtonElement} */ (el("button", { type: "button", class: "toggle", "aria-pressed": "false", title: `Only plants to start, plant or sow in the next ${GARDEN_SOON} weeks` }, "Plant soon"));
  const list = el("ul", { class: "garden-plants", "aria-label": "Plants" });
  const card = el("div", { class: "garden-card" });
  const notes = el("ul", { class: "garden-notes" });
  const water = el("div", { class: "garden-water" });
  const calendarSvg = svg("svg", { class: "garden-calendar-chart", tabindex: 0, role: "img" });
  const readout = el("div", { class: "tooltip garden-readout", hidden: "" });
  const upcoming = el("ul", { class: "garden-upcoming" });
  const table = el("div", { class: "table-wrap" });
  const calendar = el("div", { class: "garden-calendar" },
    el("h3", {}, "Calendar"), el("div", { class: "garden-calendar-key" }), el("div", { class: "plot" }, calendarSvg, readout),
    el("details", { class: "table-view" }, el("summary", {}, "Calendar as a table"), table),
    el("h3", {}, "Coming up"), upcoming);
  // The bed and its tools, beside them the plants and the one picked, and under the bed its notes; on a narrow
  // screen the plants come straight after the bed, to pick from, and the notes after them.
  const root = el("section", { class: "chart garden", "aria-labelledby": "garden-title" },
    el("div", { class: "garden-head" }, el("h2", { id: "garden-title" }, "Garden planner"), beds),
    note,
    el("div", { class: "garden-body" },
      el("div", { class: "garden-main" }, tools, plot, key, status),
      el("div", { class: "garden-side" }, el("div", { class: "garden-filters" }, search, kinds, soon), list, card),
      el("div", { class: "garden-bed-notes" }, el("h3", {}, "This bed"), notes, water)),
    calendar);
  /** @type {Planner} */
  const planner = {
    root, beds, note, tools, plot, svg: svgNode, key, status, search, kinds, soon, list, card, notes, water, calendar,
    calendarSvg, readout, upcoming, table, sunButton, months, removeButton, undoButton, clearButton, compass: compassNode,
    plants: new Map(), catalog: [], plans: {}, store: null, bed: null, brush: null,
    selected: null, moving: null, cursor: [0, 0], kind: "", onlySoon: false, undo: {}, sun: new Map(), casters: new Map(),
    sunFor: null,
    sunMonth: Math.min(9, Math.max(3, new Date().getMonth())), showSun: false, scenes: new Map(), scenesFor: null,
    row: null, drag: null, ghost: null, saving: new Map(), error: null, highlight: [],
  };
  search.addEventListener("input", () => renderPalette());
  sunButton.addEventListener("click", () => {
    planner.showSun = !planner.showSun;
    renderTools();
    drawBed();
    ensureSun();
  });
  removeButton.addEventListener("click", () => setBrush(planner.brush === "remove" ? null : "remove"));
  undoButton.addEventListener("click", () => undoBed());
  clearButton.addEventListener("click", () => {
    const bed = currentBed();
    if (bed && confirm(`Take everything out of ${bed.name}?`)) commit(bed.name, [], `Cleared ${bed.name}.`);
  });
  soon.addEventListener("click", () => {
    planner.onlySoon = !planner.onlySoon;
    renderPalette();
  });
  svgNode.addEventListener("pointerdown", bedPointerDown);
  svgNode.addEventListener("pointermove", bedPointerMove);
  svgNode.addEventListener("pointerup", bedPointerUp);
  svgNode.addEventListener("pointercancel", () => {
    planner.drag = null;
    planner.ghost = null;
    drawBed();
  });
  svgNode.addEventListener("keydown", bedKey);
  svgNode.addEventListener("focus", () => drawBed());
  svgNode.addEventListener("blur", () => drawBed());
  calendarSvg.addEventListener("pointermove", (event) => {
    const rows = calendarRows();
    const box = calendarSvg.getBoundingClientRect();
    const k = Math.floor((event.clientY - box.top - CALENDAR_TOP) / CALENDAR_ROW);
    setCalendarRow(k >= 0 && k < rows.length ? k : null);
  });
  calendarSvg.addEventListener("pointerleave", () => setCalendarRow(null));
  calendarSvg.addEventListener("blur", () => setCalendarRow(null));
  calendarSvg.addEventListener("keydown", (event) => {
    const rows = calendarRows();
    if (!rows.length) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      setCalendarRow(planner.row == null ? (step > 0 ? 0 : rows.length - 1) : (planner.row + step + rows.length) % rows.length);
    } else if (event.key === "Escape") setCalendarRow(null);
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible" || planner.store !== "server" || planner.saving.size) return;
    // Another device may have changed them meanwhile, so what's picked or moving here, by its place, is let go.
    loadPlans().then(() => {
      planner.selected = planner.moving = null;
      planner.ghost = null;
      renderPlanner();
    });
  });
  new ResizeObserver(() => {
    drawBed();
    drawCalendar();
  }).observe(root);
  return planner;
}

// The plants and the saved plans, then the planner drawn.
async function loadPlanner() {
  const planner = state.planner;
  try {
    const response = await fetch("plants.json");
    /** @type {{plants: Plant[]}} */
    const data = await response.json();
    planner.catalog = data.plants;
    planner.plants = new Map(data.plants.map((p) => [p.id, p]));
  } catch {
    planner.error = "Couldn't load the plants.";
  }
  await loadPlans();
  renderPlanner();
}

// The saved plans: the server's, or where it keeps none, this browser's.
async function loadPlans() {
  const planner = state.planner;
  try {
    const response = await fetch("api/garden");
    if (response.ok) {
      planner.plans = (await response.json()).beds;
      planner.store = "server";
      return;
    }
    if (response.status !== 404) throw new Error(String(response.status));
  } catch {
    if (planner.store === "server") return; // keep what's shown; it's tried again later
  }
  planner.store = "browser";
  try {
    planner.plans = JSON.parse(localStorage.getItem(GARDEN_SAVE_KEY) ?? "{}").beds ?? {};
  } catch {
    planner.plans = {};
  }
}

// The beds in the house's details that are planted, in soil or mulch, not gravel, soil first.
function gardenBeds() {
  /** @type {Bed[]} */
  const beds = (state.details?.grounds ?? []).filter((g) => watered(g)).map((g) => ({
    name: g.name, ground: g, w: Math.floor(g.width + 1e-6), d: Math.floor(g.depth + 1e-6), soil: g.paving === "dirt",
  }));
  return beds.filter((b) => b.w > 0 && b.d > 0).sort((a, b) => Number(b.soil) - Number(a.soil) || a.name.localeCompare(b.name));
}

function currentBed() {
  const planner = state.planner;
  const beds = gardenBeds();
  return beds.find((b) => b.name === planner.bed) ?? null;
}

/** @param {string} [bed] */
function plantingsOf(bed) {
  const name = bed ?? state.planner.bed;
  return name ? (state.planner.plans[name] ?? []).filter((p) => state.planner.plants.has(p.plant)) : [];
}

/** @param {string} id */
function plantOf(id) {
  return /** @type {Plant} */ (state.planner.plants.get(id));
}

// Everything, as when the house or the plans load.
function renderPlanner() {
  const planner = state.planner;
  if (!planner) return;
  const beds = gardenBeds();
  if (!beds.some((b) => b.name === planner.bed)) {
    let saved = null;
    try {
      saved = localStorage.getItem(GARDEN_BED_KEY);
    } catch {}
    planner.bed = (beds.find((b) => b.name === saved) ?? beds[0])?.name ?? null;
    planner.selected = planner.moving = null;
  }
  renderModelButtons(planner.beds, beds.map((b) => [b.name, b.name, `${b.w} × ${b.d} ft${b.soil ? "" : ", mulch"}`]), planner.bed ?? "", (name) => {
    planner.bed = name;
    planner.selected = planner.moving = null;
    planner.cursor = [0, 0];
    try {
      localStorage.setItem(GARDEN_BED_KEY, name);
    } catch {}
    renderPlanner();
  });
  const site = state.gardenSite;
  const says = [
    planner.error,
    !beds.length && "The house has no beds to plan: give its details a ground of the kind \"bed\", in soil or mulch.",
    beds.length && !site && "Add GARDEN to the house file, with its latitude, which way it faces and its frost dates, for the sun on each square and the dates to plant.",
    planner.store === "browser" && "Saved in this browser only, as the server here keeps no plans.",
  ].filter((s) => s);
  planner.note.textContent = says.join(" ");
  planner.note.hidden = !says.length;
  renderKinds();
  renderPalette();
  renderTools();
  drawBed();
  renderCard();
  renderNotes();
  renderWater();
  drawCalendar();
  ensureSun(); // for the notes on sun, whether or not it's shown
}

// The house as it bears on the sun and water, as JSON: its details and garden. What's worked out from it is kept
// until it changes, not just until the page loads it again.
function houseKey() {
  return JSON.stringify([state.details, state.gardenSite]);
}

// --- The palette: the plants to plant, filtered

function renderKinds() {
  const planner = state.planner;
  renderModelButtons(planner.kinds, /** @type {[string, string][]} */ (GARDEN_KINDS), planner.kind, (kind) => {
    planner.kind = kind;
    renderKinds();
    renderPalette();
  });
}

function renderPalette() {
  const planner = state.planner;
  planner.soon.setAttribute("aria-pressed", String(planner.onlySoon));
  const words = planner.search.value.trim().toLowerCase();
  const counts = new Map();
  for (const p of plantingsOf()) counts.set(p.plant, (counts.get(p.plant) ?? 0) + plantOf(p.plant).perSquare);
  const shown = planner.catalog.filter((p) => (!planner.kind || p.kind === planner.kind)
    && (!words || `${p.name} ${p.family}`.toLowerCase().includes(words))
    && (!planner.onlySoon || plantSoon(p)));
  const focused = planner.list.contains(document.activeElement) ? /** @type {HTMLElement} */ (document.activeElement).dataset.plant : null;
  planner.list.replaceChildren(...shown.map((p) => {
    const count = counts.get(p.id);
    const button = el("button", { type: "button", "aria-pressed": String(planner.brush === p.id), class: `kind-${p.kind}`, "data-plant": p.id },
      el("span", { class: "swatch", "aria-hidden": "true" }),
      el("span", { class: "name" }, p.name, ...(count ? [el("span", { class: "count" }, ` · ${count} in bed`)] : [])),
      el("span", { class: "meta" }, `${roomText(p, true)} · sun ${p.sun[0]}+ h · water ${p.water}`));
    button.addEventListener("click", () => setBrush(planner.brush === p.id ? null : p.id));
    return el("li", {}, button);
  }));
  if (focused) /** @type {HTMLElement | null} */ (planner.list.querySelector(`[data-plant="${focused}"]`))?.focus();
  if (!shown.length) planner.list.append(el("li", { class: "empty" }, planner.onlySoon ? `Nothing to start or plant in the next ${GARDEN_SOON} weeks.` : "No plant by that name."));
}

/** @param {string | null} brush */
function setBrush(brush) {
  const planner = state.planner;
  planner.brush = brush;
  planner.moving = null;
  if (brush) planner.selected = null;
  renderPalette();
  renderTools();
  renderCard();
  drawBed();
  drawCalendar();
  if (brush && brush !== "remove") say(`${plantOf(brush).name}: click or tap squares in the bed to plant it, or drag across them.`);
}

// Whether a plant is to be started, planted or sown from now to GARDEN_SOON weeks on.
/** @param {Plant} p */
function plantSoon(p) {
  const site = state.gardenSite;
  if (!site) return false;
  const now = today();
  const until = addDays(now, GARDEN_SOON * 7);
  return [seasonYear(site), seasonYear(site) + 1].some((year) => {
    const w = plantWindows(p, site, year);
    return [w.start, w.plant, w.fall].some((span) => span && span[1] >= now && span[0] <= until);
  });
}

// --- The tools over the bed: the sun, undo, clear, and which way is north

// The tools' state: pressed, shown or not, and the compass turned to north as the bed is drawn.
function renderTools() {
  const planner = state.planner;
  const bed = currentBed();
  const site = state.gardenSite;
  planner.tools.hidden = !bed;
  if (!bed) return;
  planner.sunButton.setAttribute("aria-pressed", String(planner.showSun));
  planner.sunButton.disabled = !site;
  planner.sunButton.title = site ? "Shade each square by the hours of direct sun it gets, from the house, trees and fences" : "Needs GARDEN in the house file";
  planner.months.hidden = !planner.showSun;
  renderModelButtons(planner.months, GARDEN_MONTHS.map((m) => [String(m), MONTH_NAMES[m], null]), String(planner.sunMonth), (m) => {
    planner.sunMonth = Number(m);
    renderTools();
    drawBed();
  });
  planner.removeButton.setAttribute("aria-pressed", String(planner.brush === "remove"));
  planner.undoButton.disabled = !(planner.undo[bed.name] ?? []).length;
  planner.clearButton.disabled = !plantingsOf().length;
  planner.compass.hidden = !site;
  if (site) {
    const [dx, dy] = toScreenVector(northOnPlan(site));
    const angle = (Math.atan2(dx, -dy) * 180) / Math.PI; // from straight up, clockwise
    const arrow = svg("svg", { width: 18, height: 18, viewBox: "-9 -9 18 18", "aria-hidden": "true" });
    arrow.append(svg("path", { d: "M0 -8.5 L3 7 L0 4 L-3 7 Z", transform: `rotate(${angle.toFixed(1)})` }));
    planner.compass.replaceChildren(arrow, "N");
  }
}

// --- The bed: its squares, plantings, sun, and the keyboard's square

/** @typedef {{cell: number, cols: number, rows: number, turned: boolean, bed: Bed}} BedLayout */

// How the bed is drawn: its long side across, or on a narrow screen, turned so it runs down the page.
/** @param {Bed} bed @returns {BedLayout} */
function bedLayout(bed) {
  const width = Math.max(200, state.planner.plot.clientWidth || 600);
  const across = Math.min(GARDEN_CELL[1], Math.floor(width / bed.w));
  const down = Math.min(GARDEN_CELL[1], Math.floor(width / bed.d));
  const turned = across < 40 && down > across;
  const cell = Math.max(turned ? Math.min(down, GARDEN_CELL[1]) : across, 16);
  return turned ? { cell, cols: bed.d, rows: bed.w, turned, bed } : { cell, cols: bed.w, rows: bed.d, turned, bed };
}

// A square of the bed, x along it and y from its back, to its column and row as drawn, and back. Turned, the bed's
// back is on the right and its left end at the top, as if turned a quarter clockwise.
/** @param {BedLayout} L @param {number} x @param {number} y @returns {[number, number]} */
function toScreen(L, x, y) {
  return L.turned ? [L.bed.d - 1 - y, x] : [x, y];
}
/** @param {BedLayout} L @param {number} col @param {number} row @returns {[number, number]} */
function fromScreen(L, col, row) {
  return L.turned ? [row, L.bed.d - 1 - col] : [col, row];
}
// A direction on the plan as drawn.
/** @param {number[]} v */
function toScreenVector([vx, vy]) {
  const bed = currentBed();
  const turned = bed ? bedLayout(bed).turned : false;
  return turned ? [-vy, vx] : [vx, vy];
}

// A planting's squares, as drawn: its left column, top row, and how many squares it takes on a side.
/** @param {BedLayout} L @param {Planting} p */
function screenBox(L, p) {
  const size = plantOf(p.plant).size;
  const corners = [toScreen(L, p.x, p.y), toScreen(L, p.x + size - 1, p.y + size - 1)];
  return { col: Math.min(corners[0][0], corners[1][0]), row: Math.min(corners[0][1], corners[1][1]), size };
}

function drawBed() {
  const planner = state.planner;
  const root = planner.svg;
  const bed = currentBed();
  root.replaceChildren();
  if (!bed || !planner.plants.size) {
    root.setAttribute("height", "0");
    planner.key.replaceChildren();
    return;
  }
  const L = bedLayout(bed);
  const { cell } = L;
  const [W, H] = [L.cols * cell, L.rows * cell];
  root.setAttribute("width", String(W + 4));
  root.setAttribute("height", String(H + 4));
  root.setAttribute("viewBox", `-2 -2 ${W + 4} ${H + 4}`);
  root.setAttribute("aria-label", `${bed.name}, ${bed.w} by ${bed.d} feet${L.turned ? ", turned to run down the page" : ""}. Arrow keys move between squares; Enter plants or picks, Delete takes out, M moves the plant picked, Escape lets go.`);
  const plantings = plantingsOf();
  const sun = planner.showSun ? sunHours(bed, planner.sunMonth) : null;
  const notes = bedNotes(bed, plantings);
  const warned = new Set(notes.filter((n) => n.level === "warn").flatMap((n) => n.at));
  // The soil, a square a foot, shaded by its sun when that's shown.
  const ground = svg("g", { class: "squares" });
  for (let col = 0; col < L.cols; col++) {
    for (let row = 0; row < L.rows; row++) {
      const [x, y] = fromScreen(L, col, row);
      const hours = sun ? sun[y * bed.w + x] : null;
      const bin = hours == null ? null : GARDEN_SUN_BINS.filter((b) => hours >= b).length;
      ground.append(svg("rect", { x: col * cell, y: row * cell, width: cell, height: cell, class: bin == null ? "soil" : `sun-${bin}` }));
      if (hours != null) {
        const text = svg("text", { x: col * cell + 4, y: row * cell + 12, class: "hours" });
        text.textContent = hours.toFixed(1);
        ground.append(text);
      }
    }
  }
  root.append(ground, svg("rect", { x: 0, y: 0, width: W, height: H, class: "edging" }));
  // The plantings, each a tile over its squares: its name, how many it holds, and a warning sign if a note warns of it.
  plantings.forEach((p, k) => {
    const plant = plantOf(p.plant);
    const { col, row, size } = screenBox(L, p);
    const [x, y, s] = [col * cell, row * cell, size * cell];
    const classes = ["planting", `kind-${plant.kind}`, k === planner.selected && "selected", planner.highlight.includes(k) && "lit", planner.moving === k && "moving", sun && "over-sun"];
    const g = svg("g", { class: classes.filter((c) => c).join(" ") });
    const title = svg("title");
    title.textContent = `${plant.name}${plant.perSquare > 1 ? `, ${plant.perSquare} in this square` : ""}`;
    g.append(title, svg("rect", { x: x + 2, y: y + 2, width: s - 4, height: s - 4, rx: 6 }));
    if (plant.perSquare > 1 && !sun) {
      const n = Math.ceil(Math.sqrt(plant.perSquare));
      for (let i = 0; i < plant.perSquare; i++) {
        const [cx, cy] = [x + ((i % n) + 0.5) * (s / n), y + (Math.floor(i / n) + 0.5) * (s / n)];
        g.append(svg("circle", { cx, cy, r: Math.max(1.5, s / n / 7), class: "seed" }));
      }
    }
    const lines = tileLines(plant.name, s - 8, size > 1 ? 3 : 2, cell < 44 ? 10 : 11);
    const text = svg("text", { x: x + s / 2, y: y + s / 2 - ((lines.length - 1) * 12) / 2 + 4, class: "label", "text-anchor": "middle", "font-size": cell < 44 ? 10 : 11 });
    lines.forEach((line, i) => {
      const span = svg("tspan", { x: x + s / 2, dy: i ? 12 : 0 });
      span.textContent = line;
      text.append(span);
    });
    g.append(text);
    if (plant.perSquare > 1 && cell >= 40) {
      const count = svg("text", { x: x + s - 6, y: y + s - 6, class: "count", "text-anchor": "end" });
      count.textContent = `×${plant.perSquare}`;
      g.append(count);
    }
    if (warned.has(k)) {
      const badge = svg("text", { x: x + s - 6, y: y + 15, class: "badge", "text-anchor": "end" });
      badge.textContent = "⚠";
      g.append(badge);
    }
    root.append(g);
  });
  // Where a dragged or moved plant would go, and the keyboard's square.
  if (planner.ghost) {
    const ghost = planner.ghost;
    const size = ghost.size;
    const [a, b] = [toScreen(L, ghost.x, ghost.y), toScreen(L, ghost.x + size - 1, ghost.y + size - 1)];
    const fits = ghost.x >= 0 && ghost.y >= 0 && ghost.x + size <= bed.w && ghost.y + size <= bed.d;
    root.append(svg("rect", { x: Math.min(a[0], b[0]) * cell + 1, y: Math.min(a[1], b[1]) * cell + 1, width: size * cell - 2, height: size * cell - 2, rx: 6, class: `ghost${fits ? "" : " misfit"}` }));
  }
  if (document.activeElement === root) {
    const [col, row] = toScreen(L, ...planner.cursor);
    root.append(svg("rect", { x: col * cell + 1, y: row * cell + 1, width: cell - 2, height: cell - 2, class: "cursor" }));
  }
  renderSunKey(sun != null || (planner.showSun && planner.sun.get(bed.name) === "working"));
}

// The tile's name in lines that fit its width, each word whole but a long one cut short.
/** @param {string} name @param {number} width @param {number} most @param {number} size */
function tileLines(name, width, most, size) {
  const fit = Math.max(3, Math.floor(width / (size * 0.55)));
  /** @type {string[]} */
  const lines = [];
  for (const word of name.split(" ")) {
    const last = lines.at(-1);
    if (last != null && `${last} ${word}`.length <= fit) lines[lines.length - 1] = `${last} ${word}`;
    else lines.push(word.length > fit ? `${word.slice(0, fit - 1)}…` : word);
  }
  return lines.length > most ? [...lines.slice(0, most - 1), `${lines[most - 1].slice(0, fit - 1)}…`] : lines;
}

// The key under the bed: the plants' kinds, and while the sun shows, what its shades mean.
/** @param {boolean} sun */
function renderSunKey(sun) {
  const planner = state.planner;
  const bed = currentBed();
  const working = bed && planner.sun.get(bed.name) === "working";
  const kinds = el("ul", { class: "coverage-key", "aria-label": "Kinds of plants" },
    ...GARDEN_KINDS.slice(1).map(([kind, label]) => el("li", { class: `kind-${kind}` }, el("span", { class: "swatch" }), label)));
  /** @type {HTMLElement[]} */
  const parts = [kinds];
  if (sun) {
    parts.push(working
      ? el("span", { class: "coverage-unit" }, "Working out the sun from the house, trees and fences…")
      : el("ul", { class: "coverage-key", "aria-label": `Hours of direct sun each square gets in ${MONTH_NAMES[planner.sunMonth]}` },
        ...GARDEN_SUN_KEY.map((label, n) => el("li", {}, el("span", { class: `swatch sun-${n}` }), label)),
        el("li", { class: "coverage-unit" }, `of direct sun a day in mid-${MONTH_NAMES[planner.sunMonth]}`)));
  }
  planner.key.replaceChildren(...parts);
}

// --- Planting: by pointer, a click, tap or drag, and by keyboard

/** @param {PointerEvent} event @returns {[number, number] | null} */
function squareAt(event) {
  const bed = currentBed();
  if (!bed) return null;
  const L = bedLayout(bed);
  const box = state.planner.svg.getBoundingClientRect();
  const [col, row] = [Math.floor((event.clientX - box.left - 2) / L.cell), Math.floor((event.clientY - box.top - 2) / L.cell)];
  if (col < 0 || row < 0 || col >= L.cols || row >= L.rows) return null;
  return fromScreen(L, col, row);
}

/** @param {Planting[]} plantings @param {number} x @param {number} y */
function plantingAt(plantings, x, y) {
  return plantings.findIndex((p) => {
    const size = plantOf(p.plant).size;
    return x >= p.x && x < p.x + size && y >= p.y && y < p.y + size;
  });
}

/** @param {PointerEvent} event */
function bedPointerDown(event) {
  const planner = state.planner;
  const bed = currentBed();
  const at = squareAt(event);
  if (!bed || !at || event.button !== 0) return;
  const plantings = plantingsOf();
  planner.cursor = at;
  // Focused by hand, and the press's own default left out: drawing the bed again takes the square pressed out from
  // under it, and the browser, finding it gone, would move the focus off the bed. That keeps a drag from selecting
  // text too.
  event.preventDefault();
  planner.svg.focus({ preventScroll: true });
  if (planner.moving != null) {
    movePlanting(planner.moving, at[0], at[1]);
    return;
  }
  const k = plantingAt(plantings, ...at);
  if (planner.brush) {
    // Planting or taking out, here and wherever the pointer's dragged to before it lets go, as one step to undo.
    planner.svg.setPointerCapture(event.pointerId);
    planner.drag = { x: event.clientX, y: event.clientY, at: -1, dx: 0, dy: 0, moved: false, paint: planner.brush, painted: new Set(), changed: false, before: plantings };
    paint(at);
    return;
  }
  planner.selected = k >= 0 ? k : null;
  if (k >= 0 && event.pointerType !== "touch") {
    planner.svg.setPointerCapture(event.pointerId);
    planner.drag = { x: event.clientX, y: event.clientY, at: k, dx: at[0] - plantings[k].x, dy: at[1] - plantings[k].y, moved: false, paint: null, painted: new Set(), changed: false, before: plantings };
  }
  renderCard();
  drawBed();
  drawCalendar();
}

/** @param {PointerEvent} event */
function bedPointerMove(event) {
  const planner = state.planner;
  const drag = planner.drag;
  const at = squareAt(event);
  if (!drag) {
    if (planner.moving != null && at) {
      planner.ghost = { x: at[0], y: at[1], size: plantOf(plantingsOf()[planner.moving].plant).size };
      drawBed();
    }
    return;
  }
  if (!at) return;
  if (drag.paint) {
    paint(at);
    return;
  }
  if (!drag.moved && Math.hypot(event.clientX - drag.x, event.clientY - drag.y) < 5) return;
  drag.moved = true;
  const p = plantingsOf()[drag.at];
  planner.ghost = { x: at[0] - drag.dx, y: at[1] - drag.dy, size: plantOf(p.plant).size };
  drawBed();
}

/** @param {PointerEvent} event */
function bedPointerUp(event) {
  const planner = state.planner;
  const drag = planner.drag;
  planner.drag = null;
  if (!drag) return;
  const bed = /** @type {Bed} */ (currentBed());
  if (drag.paint) {
    // The step to undo is from before the drag began.
    if (drag.changed) pushUndo(bed.name, drag.before);
    saveBed(bed.name);
    renderPlanner();
    return;
  }
  const at = squareAt(event);
  planner.ghost = null;
  if (drag.moved && at) movePlanting(drag.at, at[0] - drag.dx, at[1] - drag.dy);
  else drawBed();
}

// The brush applied at a square while painting: a plant planted there, or what's there taken out.
/** @param {[number, number]} at */
function paint([x, y]) {
  const planner = state.planner;
  const drag = /** @type {NonNullable<Planner["drag"]>} */ (planner.drag);
  const bed = /** @type {Bed} */ (currentBed());
  const key = `${x},${y}`;
  if (drag.painted.has(key)) return;
  drag.painted.add(key);
  const plantings = plantingsOf();
  if (drag.paint === "remove") {
    const k = plantingAt(plantings, x, y);
    if (k < 0) return;
    say(`Took out ${plantOf(plantings[k].plant).name}.`);
    setPlantings(bed.name, plantings.filter((_, i) => i !== k));
    drag.changed = true;
  } else {
    const plant = plantOf(/** @type {string} */ (drag.paint));
    const size = plant.size;
    const [px, py] = [Math.min(x, bed.w - size), Math.min(y, bed.d - size)];
    if (px < 0 || py < 0) return say(`${plant.name} takes ${size} × ${size} ft, more than this bed has.`);
    // A big plant painted again where it already is leaves it; else it takes the squares, whatever was in them.
    if (plantings.some((p) => p.plant === plant.id && p.x === px && p.y === py)) return;
    const kept = plantings.filter((p) => !overlaps(p, { plant: plant.id, x: px, y: py }));
    for (let dx = 0; dx < size; dx++) for (let dy = 0; dy < size; dy++) drag.painted.add(`${px + dx},${py + dy}`);
    say(`Planted ${plant.name}${plant.perSquare > 1 ? `, ${plant.perSquare} to the square foot` : ""}.`);
    setPlantings(bed.name, [...kept, { plant: plant.id, x: px, y: py }]);
    drag.changed = true;
  }
  drawBed();
}

/** @param {Planting} a @param {Planting} b */
function overlaps(a, b) {
  const [sa, sb] = [plantOf(a.plant).size, plantOf(b.plant).size];
  return a.x < b.x + sb && b.x < a.x + sa && a.y < b.y + sb && b.y < a.y + sa;
}

// A planting moved so its back-left square is at x, y, taking those squares, if it fits in the bed.
/** @param {number} k @param {number} x @param {number} y */
function movePlanting(k, x, y) {
  const planner = state.planner;
  const bed = /** @type {Bed} */ (currentBed());
  const plantings = plantingsOf();
  const p = plantings[k];
  const plant = plantOf(p.plant);
  planner.moving = null;
  planner.ghost = null;
  if (x < 0 || y < 0 || x + plant.size > bed.w || y + plant.size > bed.d) {
    say(`${plant.name} doesn't fit there.`);
    drawBed();
    return;
  }
  if (x === p.x && y === p.y) return drawBed();
  const moved = { ...p, x, y };
  const kept = plantings.filter((q, i) => i !== k && !overlaps(q, moved));
  planner.selected = kept.length;
  commit(bed.name, [...kept, moved], `Moved ${plant.name}.`);
}

/** @param {KeyboardEvent} event */
function bedKey(event) {
  const planner = state.planner;
  const bed = currentBed();
  if (!bed) return;
  const L = bedLayout(bed);
  const plantings = plantingsOf();
  const [col, row] = toScreen(L, ...planner.cursor);
  const steps = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
  const step = steps[/** @type {keyof typeof steps} */ (event.key)];
  if (step) {
    event.preventDefault();
    const [c, r] = [Math.min(L.cols - 1, Math.max(0, col + step[0])), Math.min(L.rows - 1, Math.max(0, row + step[1]))];
    planner.cursor = fromScreen(L, c, r);
    const k = plantingAt(plantings, ...planner.cursor);
    if (planner.moving != null) planner.ghost = { x: planner.cursor[0], y: planner.cursor[1], size: plantOf(plantings[planner.moving].plant).size };
    say(k >= 0 ? plantOf(plantings[k].plant).name : `Empty square, ${planner.cursor[0] + 1} along and ${planner.cursor[1] + 1} from the back`);
    drawBed();
  } else if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    const k = plantingAt(plantings, ...planner.cursor);
    if (planner.moving != null) movePlanting(planner.moving, ...planner.cursor);
    else if (planner.brush) {
      const drag = { x: 0, y: 0, at: -1, dx: 0, dy: 0, moved: false, paint: planner.brush, painted: new Set(), changed: false, before: plantings };
      planner.drag = drag;
      paint(planner.cursor);
      planner.drag = null;
      if (drag.changed) pushUndo(bed.name, plantings);
      saveBed(bed.name);
      renderPlanner();
    } else {
      planner.selected = k >= 0 ? k : null;
      renderCard();
      drawBed();
      drawCalendar();
    }
  } else if (event.key === "Delete" || event.key === "Backspace") {
    event.preventDefault();
    const k = plantingAt(plantings, ...planner.cursor);
    if (k >= 0) commit(bed.name, plantings.filter((_, i) => i !== k), `Took out ${plantOf(plantings[k].plant).name}.`);
  } else if ((event.key === "m" || event.key === "M") && planner.selected != null) {
    startMove(planner.selected);
  } else if (event.key === "Escape") {
    if (planner.moving != null || planner.brush || planner.selected != null) event.preventDefault();
    planner.moving = null;
    planner.ghost = null;
    if (planner.brush) setBrush(null);
    else {
      planner.selected = null;
      renderCard();
      drawBed();
    }
  } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") {
    event.preventDefault();
    undoBed();
  }
}

/** @param {number} k */
function startMove(k) {
  const planner = state.planner;
  const p = plantingsOf()[k];
  planner.moving = k;
  planner.brush = null;
  planner.ghost = { x: p.x, y: p.y, size: plantOf(p.plant).size };
  planner.cursor = [p.x, p.y];
  say(`Moving ${plantOf(p.plant).name}: click, tap or press Enter on the square for its back-left corner, or Escape to leave it.`);
  renderPalette();
  renderTools();
  planner.svg.focus();
  drawBed();
}

// --- Changes, undo and saving

/** @param {string} bed @param {Planting[]} plantings */
function setPlantings(bed, plantings) {
  state.planner.plans = { ...state.planner.plans, [bed]: plantings };
}

/** @param {string} bed @param {Planting[]} before */
function pushUndo(bed, before) {
  const undo = (state.planner.undo[bed] ??= []);
  undo.push(before);
  if (undo.length > GARDEN_UNDO) undo.shift();
}

// A bed's plantings changed as one step to undo, saved, and said.
/** @param {string} bed @param {Planting[]} plantings @param {string} said */
function commit(bed, plantings, said) {
  pushUndo(bed, plantingsOf(bed));
  setPlantings(bed, plantings);
  if (state.planner.selected != null && state.planner.selected >= plantings.length) state.planner.selected = null;
  saveBed(bed);
  say(said);
  renderPlanner();
}

function undoBed() {
  const planner = state.planner;
  const bed = planner.bed;
  const before = bed && planner.undo[bed]?.pop();
  if (!bed || !before) return;
  setPlantings(bed, before);
  planner.selected = planner.moving = null;
  saveBed(bed);
  say("Undone.");
  renderPlanner();
}

// A bed's plantings saved a moment after the last change, to the server or this browser.
/** @param {string} bed */
function saveBed(bed) {
  const planner = state.planner;
  clearTimeout(planner.saving.get(bed));
  planner.saving.set(bed, setTimeout(async () => {
    const plants = plantingsOf(bed);
    try {
      if (planner.store === "server") {
        const response = await fetch("api/garden", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ bed, plants }) });
        if (!response.ok) throw new Error(`the server said ${response.status}`);
      } else {
        localStorage.setItem(GARDEN_SAVE_KEY, JSON.stringify({ beds: planner.plans }));
      }
      if (planner.error?.startsWith("Couldn't save")) planner.error = null;
    } catch (e) {
      planner.error = `Couldn't save ${bed}: ${e instanceof Error ? e.message : e}. It's kept on this page; it'll save with the next change.`;
    }
    planner.saving.delete(bed);
    renderPlanner();
  }, 400));
}

/** @param {string} text */
function say(text) {
  state.planner.status.textContent = text;
}

// --- The card: the plant picked or planted, all about it

function renderCard() {
  const planner = state.planner;
  const plantings = plantingsOf();
  const picked = planner.selected != null ? plantings[planner.selected] : null;
  const id = picked?.plant ?? (planner.brush && planner.brush !== "remove" ? planner.brush : null);
  if (!id) {
    planner.card.replaceChildren(el("p", { class: "hint" }, "Pick a plant above to see what it needs and plant it, or pick one in the bed."));
    return;
  }
  const p = plantOf(id);
  const site = state.gardenSite;
  /** @param {string} name @param {...(Node | string)} value */
  const row = (name, ...value) => el("div", { class: "row" }, el("dt", {}, name), el("dd", {}, ...value));
  const rows = [
    row("Room", roomText(p)),
    row("Height", `about ${feetInches(p.height)}`),
    row("Sun", `${p.sun[0]}${p.sun[1] > p.sun[0] ? `–${p.sun[1]}` : ""} h of direct sun a day${p.sun[0] >= 6 ? " (full sun)" : p.sun[0] >= 4 ? " (part sun)" : " (part shade)"}`),
    row("Water", `${p.water[0].toUpperCase()}${p.water.slice(1)}, about ${inches(GARDEN_WATER[p.water])} a week`),
    row("Season", `${p.season === "cool" ? "Cool season" : "Warm season"}; ${{ tender: "frost kills it", light: "takes a light frost", hard: "takes a hard frost" }[p.frost]}${p.perennial ? "; comes back every year" : ""}`),
    ...whenRows(p, site, row),
    row("Grows", p.food ? (p.kind === "flower" ? "Food too: its flowers or seeds are good to eat" : "Food") : "Flowers, not food"),
    ...(p.trellis ? [row("Needs", "A trellis, stakes or a cage")] : []),
  ];
  const friends = relations(p.id, "companions");
  const foes = relations(p.id, "avoid");
  const inBed = new Set(plantings.map((q) => q.plant));
  /** @param {string[]} ids */
  const names = (ids) => ids.length ? ids.map((q, i) => [i ? ", " : "", inBed.has(q) ? el("strong", {}, plantOf(q).name) : plantOf(q).name]).flat() : ["—"];
  rows.push(row("Grows well with", ...names(friends)), row("Keep it from", ...names(foes)));
  const head = el("div", { class: "card-head" }, el("h3", {}, p.name), el("span", { class: "meta" }, `${p.kind[0].toUpperCase()}${p.kind.slice(1)} · ${p.family}`));
  const parts = [head, el("dl", {}, ...rows), el("p", { class: "tips" }, p.tips)];
  if (picked) {
    const k = /** @type {number} */ (planner.selected);
    const here = sunAt(picked);
    const said = bedNotes(/** @type {Bed} */ (currentBed()), plantings).filter((n) => n.at.includes(k) && n.level !== "tip");
    if (here != null) parts.splice(2, 0, el("p", { class: "here" }, `Here it gets about ${here.toFixed(1)} h of sun a day in ${monthsText(growingMonths(p))}.`));
    if (said.length) parts.splice(2, 0, noteList(said));
    const move = el("button", { type: "button" }, "Move");
    move.addEventListener("click", () => startMove(k));
    const remove = el("button", { type: "button" }, "Take out");
    remove.addEventListener("click", () => {
      planner.selected = null;
      commit(/** @type {string} */ (planner.bed), plantings.filter((_, i) => i !== k), `Took out ${p.name}.`);
    });
    parts.push(el("div", { class: "card-actions" }, move, remove));
  }
  planner.card.replaceChildren(...parts);
}

// When to start, plant and harvest a plant, as rows: dates with the house's frost dates, else weeks from them.
/** @param {Plant} p @param {Garden | null} site @param {(name: string, ...value: (Node | string)[]) => HTMLElement} row */
function whenRows(p, site, row) {
  /** @param {number[]} weeks @param {string} frost */
  const relative = ([a, b], frost) => `${weeksText(a)} to ${weeksText(b)} the ${frost}`.replace(/^(\S+)/, (w) => w[0].toUpperCase() + w.slice(1));
  if (!site) {
    return [
      ...(p.start ? [row("Start indoors", relative(p.start, "last frost"))] : []),
      ...(p.plant ? [row(plantVerb(p), relative(p.plant, "last frost"))] : []),
      ...(p.fall ? [row(p.plant ? "For fall" : plantVerb(p), relative(p.fall, "first frost"))] : []),
      row(p.food ? "Harvest" : "Blooms", `${p.days[0]}–${p.days[1]} days after, for about ${p.harvest} weeks`),
    ];
  }
  const w = plantWindows(p, site, seasonYear(site));
  return [
    ...(w.start ? [row("Start indoors", spanText(w.start))] : []),
    ...(w.plant ? [row(plantVerb(p), spanText(w.plant))] : []),
    ...(w.fall ? [row(p.plant ? "For fall" : plantVerb(p), spanText(w.fall))] : []),
    row(p.food ? "Harvest" : "Blooms", `${p.days[0]}–${p.days[1]} days after: ${w.harvest.map((h) => spanText(h, true)).join(", then ") || "—"}`),
  ];
}

/** @param {Plant} p */
function plantVerb(p) {
  return { seed: "Sow", transplant: "Set out", either: "Sow or set out", set: "Plant" }[p.how];
}

/** @param {Plant} p @param {boolean} [short] */
function roomText(p, short = false) {
  if (p.size > 1) return short ? `${p.size}×${p.size} ft` : `One takes ${p.size} × ${p.size} ft`;
  if (short) return `${p.perSquare} a sq ft`;
  return p.perSquare === 1 ? "One to a square foot" : `${p.perSquare} to a square foot, about ${Math.round(12 / Math.sqrt(p.perSquare))} in apart`;
}

// The plants it grows well with, or is best kept from, whichever of the two says so.
/** @param {string} id @param {"companions" | "avoid"} which */
function relations(id, which) {
  const planner = state.planner;
  const own = planner.plants.get(id)?.[which] ?? [];
  const theirs = planner.catalog.filter((p) => p[which].includes(id)).map((p) => p.id);
  return [...new Set([...own, ...theirs])].filter((q) => planner.plants.has(q)).sort((a, b) => plantOf(a).name.localeCompare(plantOf(b).name));
}

/** @param {string} a @param {string} b @param {"companions" | "avoid"} which */
function related(a, b, which) {
  return plantOf(a)[which].includes(b) || plantOf(b)[which].includes(a);
}

// --- Notes on the bed: neighbors, sun, shade, water and more

/** @param {Bed} bed @param {Planting[]} plantings @returns {GardenNote[]} */
function bedNotes(bed, plantings) {
  /** @type {GardenNote[]} */
  const notes = [];
  if (!plantings.length) return notes;
  const site = state.gardenSite;
  /** @param {Planting} a @param {Planting} b */
  const gap = (a, b) => {
    const [sa, sb] = [plantOf(a.plant).size, plantOf(b.plant).size];
    return Math.max(0, b.x - (a.x + sa), a.x - (b.x + sb), b.y - (a.y + sa), a.y - (b.y + sb));
  };
  // Neighbors: kept-apart plants within a square of each other, and good ones side by side, each pair of plants once.
  /** @type {Map<string, GardenNote>} */
  const pairs = new Map();
  plantings.forEach((a, i) => plantings.forEach((b, j) => {
    if (j <= i || a.plant === b.plant) return;
    const apart = gap(a, b);
    const [first, second] = [a.plant, b.plant].sort((m, n) => plantOf(m).name.localeCompare(plantOf(n).name));
    const [one, two] = [plantOf(first).name, plantOf(second).name.toLowerCase()];
    if (apart <= 1 && related(a.plant, b.plant, "avoid")) {
      const key = `avoid ${first} ${second}`;
      const note = pairs.get(key) ?? { level: "warn", text: `${one} and ${two} are close together, and they're best kept apart.`, at: [] };
      note.at.push(i, j);
      pairs.set(key, note);
    } else if (apart === 0 && related(a.plant, b.plant, "companions")) {
      const key = `good ${first} ${second}`;
      const note = pairs.get(key) ?? { level: "good", text: `${one} and ${two} grow well together.`, at: [] };
      note.at.push(i, j);
      pairs.set(key, note);
    }
  }));
  notes.push(...pairs.values());
  // Sun: a plant getting less than it needs, in the months it grows.
  /** @type {Map<string, GardenNote & {least: number}>} */
  const short = new Map();
  plantings.forEach((p, k) => {
    const hours = sunAt(p);
    const plant = plantOf(p.plant);
    if (hours == null || hours >= plant.sun[0]) return;
    const note = short.get(p.plant) ?? { level: "warn", text: "", at: [], least: hours };
    note.least = Math.min(note.least, hours);
    note.at.push(k);
    note.text = `${plant.name} gets about ${note.least.toFixed(1)} h of sun a day there in ${monthsText(growingMonths(plant))}; it needs ${plant.sun[0]} or more.`;
    short.set(p.plant, note);
  });
  notes.push(...[...short.values()].map(({ level, text, at }) => ({ level, text, at })));
  // Shade: a much taller plant just south of a shorter one shades it, which helps one that likes it cool. Each tall
  // plant's said once, for all it shades, those that mind it and those that don't.
  if (site) {
    const north = northOnPlan(site);
    const noon = Math.max(10, 90 - Math.abs(site.latitude)); // the sun's height at noon around the equinoxes
    /** @type {Map<string, {tall: Plant, cool: boolean, shaded: Set<string>, at: Set<number>}>} */
    const shades = new Map();
    plantings.forEach((a, i) => plantings.forEach((b, j) => {
      const [pa, pb] = [plantOf(a.plant), plantOf(b.plant)];
      if (i === j || pa.height < pb.height + 24 || pa.height < 2 * pb.height) return;
      const [ca, cb] = [[a.x + pa.size / 2, a.y + pa.size / 2], [b.x + pb.size / 2, b.y + pb.size / 2]];
      const [dx, dy] = [cb[0] - ca[0], cb[1] - ca[1]];
      const distance = Math.hypot(dx, dy) - (pa.size + pb.size) / 2;
      const reach = Math.min(6, (pa.height - pb.height) / 12 / Math.tan((noon * Math.PI) / 180));
      if (distance > reach || (dx * north[0] + dy * north[1]) < Math.cos(Math.PI / 3) * Math.hypot(dx, dy)) return;
      const cool = pb.sun[0] < 5;
      const key = `${pa.id} ${cool}`;
      const shade = shades.get(key) ?? { tall: pa, cool, shaded: new Set(), at: new Set([i]) };
      shade.shaded.add(pb.name.toLowerCase());
      shade.at.add(i).add(j);
      shades.set(key, shade);
    }));
    for (const { tall, cool, shaded, at } of shades.values()) {
      const them = listText([...shaded]);
      notes.push({
        level: cool ? "tip" : "warn",
        text: cool
          ? `${tall.name} shades the ${them} north of it for part of the day, which keeps ${shaded.size > 1 ? "them" : "it"} cooler in summer.`
          : `${tall.name} stands south of the ${them} and will shade ${shaded.size > 1 ? "them" : "it"} for part of the day; put the tall plants on the bed's north side.`,
        at: [...at],
      });
    }
  }
  const ids = [...new Set(plantings.map((p) => p.plant))];
  const plants = ids.map(plantOf);
  // Water: one bed, watered alike, with plants that want much and little.
  const dry = plants.filter((p) => p.water === "low");
  const wet = plants.filter((p) => p.water === "high");
  if (dry.length && wet.length) {
    notes.push({
      level: "warn",
      text: `${listText(dry.map((p, i) => (i ? p.name.toLowerCase() : p.name)))} want${dry.length > 1 ? "" : "s"} little water and ${listText(wet.map((p) => p.name.toLowerCase()))} a lot, but a bed's watered alike: a bed of their own suits the dry ones.`,
      at: plantings.flatMap((p, k) => (dry.some((d) => d.id === p.plant) ? [k] : [])),
    });
  }
  // A trellis along the north edge shades nothing else.
  if (site) {
    const edge = northEdge(site);
    for (const p of plants.filter((q) => q.trellis && q.height >= 36)) {
      const off = plantings.flatMap((q, k) => {
        if (q.plant !== p.id) return [];
        const along = { back: q.y === 0, front: q.y + p.size === bed.d, left: q.x === 0, right: q.x + p.size === bed.w }[edge];
        return along ? [] : [k];
      });
      if (off.length) notes.push({ level: "tip", text: `${p.name} grows tall on a trellis: along the bed's north edge (its ${edge}) it won't shade the rest.`, at: off });
    }
  }
  const corn = plantings.filter((p) => p.plant === "corn").length;
  if (corn && corn < 9) notes.push({ level: "tip", text: "Corn is pollinated by the wind: a block of 9 or more squares, 3 or 4 deep, fills its ears better than a few plants.", at: plantings.flatMap((p, k) => (p.plant === "corn" ? [k] : [])) });
  if (site) {
    const year = seasonYear(site);
    const frost = frostDates(site, year)[1];
    for (const p of plants.filter((q) => q.frost === "tender" && q.plant)) {
      const ready = addDays(/** @type {Span} */ (plantWindows(p, site, year).plant)[0], p.days[1]);
      if (ready > addDays(frost, -14)) notes.push({ level: "warn", text: `${p.name} needs up to ${p.days[1]} days, so it may not ripen before the first frost (${dateText(frost)}): set it out early, or pick a quick variety.`, at: plantings.flatMap((q, k) => (q.plant === p.id ? [k] : [])) });
    }
  }
  for (const p of plants.filter((q) => q.id === "mint")) notes.push({ level: "tip", text: "Mint spreads by runners: sink it in a pot so it doesn't take over the bed.", at: plantings.flatMap((q, k) => (q.plant === p.id ? [k] : [])) });
  const perennials = plants.filter((p) => p.perennial);
  if (perennials.length && perennials.length < plants.length) {
    notes.push({ level: "tip", text: `${listText(perennials.map((p, i) => (i ? p.name.toLowerCase() : p.name)))} come${perennials.length > 1 ? "" : "s"} back every year: at one end of the bed, ${perennials.length > 1 ? "they're" : "it's"} out of the way when you replant the rest.`, at: plantings.flatMap((q, k) => (plantOf(q.plant).perennial ? [k] : [])) });
  }
  return notes;
}

/** @param {GardenNote[]} notes */
function noteList(notes) {
  const icons = { warn: "⚠", good: "✓", tip: "•" };
  return el("ul", { class: "garden-notes" }, ...notes.map((n) => el("li", { class: n.level }, el("span", { class: "icon", "aria-hidden": "true" }, icons[n.level]), n.text)));
}

function renderNotes() {
  const planner = state.planner;
  const bed = currentBed();
  if (!bed || !planner.plants.size) return planner.notes.replaceChildren();
  const plantings = plantingsOf();
  const used = plantings.reduce((n, p) => n + plantOf(p.plant).size ** 2, 0);
  const food = [...new Set(plantings.filter((p) => plantOf(p.plant).food).map((p) => plantOf(p.plant).name))];
  const notes = bedNotes(bed, plantings);
  const order = { warn: 0, good: 1, tip: 2 };
  notes.sort((a, b) => order[a.level] - order[b.level]);
  const list = noteList(notes);
  list.querySelectorAll("li").forEach((li, i) => {
    li.addEventListener("pointerenter", () => highlight(notes[i].at));
    li.addEventListener("pointerleave", () => highlight([]));
  });
  const summary = [
    `${used} of ${bed.w * bed.d} square feet planted${plantings.length ? "" : ": pick a plant, then click or tap squares to plant it"}.`,
    ...(food.length ? [`Food: ${listText(food.map((n, i) => (i ? n.toLowerCase() : n)))}.`] : []),
  ];
  planner.notes.replaceChildren(...summary.map((s) => el("li", { class: "summary" }, s)), ...list.children);
}

/** @param {number[]} at */
function highlight(at) {
  state.planner.highlight = at;
  drawBed();
}

// --- Water: what the plants want, and what the sprinklers put on the bed

function renderWater() {
  const planner = state.planner;
  const bed = currentBed();
  const plantings = plantingsOf();
  if (!bed || !plantings.length) return planner.water.replaceChildren();
  const wants = plantings.map((p) => GARDEN_WATER[plantOf(p.plant).water]);
  const [lo, hi] = [Math.min(...wants), Math.max(...wants)];
  const need = lo === hi ? inches(lo) : `${inches(lo)} to ${inches(hi)}`;
  /** @type {HTMLElement[]} */
  const parts = [el("p", {}, `These plants want about ${need} of water a week, rain included. An hour of each zone puts on this bed:`)];
  for (const { name, zones } of bedWater(bed)) {
    if (!zones.length) {
      parts.push(el("p", {}, `${name}: no sprinkler or drip line reaches it.`));
      continue;
    }
    const most = zones[0];
    const minutes = [lo, hi].map((v) => Math.round((v / most.rate) * 60 / 5) * 5);
    parts.push(
      el("p", { class: "system" }, name),
      el("ul", {}, ...zones.map((z) => el("li", {}, el("span", { class: "amount" }, inches(z.rate, true)), z.name))),
      el("p", {}, `On its own, ${most.name.replace(/ \(.*\)$/, "")} would take about ${minutes[0] === minutes[1] ? minutes[0] : `${minutes[0]}–${minutes[1]}`} minutes a week.`),
    );
  }
  planner.water.replaceChildren(el("h3", {}, "Water"), ...parts);
}

// The water an hour of each zone puts on a bed, on average over its squares, with the sprinklers as they are and in
// each proposal, the zones that reach it most first.
/** @param {Bed} bed */
function bedWater(bed) {
  const planner = state.planner;
  const details = state.details;
  const proposals = details?.proposals ?? [];
  if (!details || !details.sprinklers.length && !proposals.length) return [];
  const key = houseKey();
  if (planner.scenesFor !== key) {
    planner.scenes = new Map();
    planner.scenesFor = key;
  }
  const systems = [{ name: "As it is", sprinklers: details.sprinklers }, ...proposals.map((p) => ({ name: `With ${p.name.toLowerCase()}`, sprinklers: p.sprinklers }))];
  return systems.map(({ name, sprinklers }) => {
    if (!planner.scenes.has(name)) {
      planner.scenes.set(name, modelScene({ rooms: indoors(), areas: state.areas, details: { ...details, sprinklers }, wall: state.wall ?? MODEL_WALL, colors: modelColors }));
    }
    const scene = /** @type {Scene | NoScene} */ (planner.scenes.get(name));
    const zones = (scene.sprinklers ?? []).map((z) => {
      let total = 0;
      for (let x = 0; x < bed.w; x++) {
        for (let y = 0; y < bed.d; y++) {
          const [px, py] = [bed.ground.x + x + 0.5, bed.ground.y + y + 0.5];
          for (const h of z.heads) if (Math.hypot(px - h.x, py - h.y) <= h.radius && modelArc(h.arc)(px - h.x, py - h.y)) total += h.rate;
          for (const d of z.drips) if (d.runs.some((run) => run.some((q, k) => k > 0 && inStrip(px, py, run[k - 1], q, d.width / 2)))) total += d.rate;
        }
      }
      return { name: z.name, rate: total / (bed.w * bed.d) };
    }).filter((z) => z.rate >= 0.01).sort((a, b) => b.rate - a.rate);
    return { name, zones };
  });
}

// --- The sun: how many hours of direct sun each square gets, the house, trees and fences shading it

// Which way north is on the plan, as a direction, from the way the plan's front faces.
/** @param {Garden} site @returns {number[]} */
function northOnPlan(site) {
  const angle = ((90 - site.facing) * Math.PI) / 180;
  return [Math.cos(angle), Math.sin(angle)];
}

// The bed's side that faces most nearly north.
/** @param {Garden} site @returns {"back" | "front" | "left" | "right"} */
function northEdge(site) {
  const [nx, ny] = northOnPlan(site);
  return Math.abs(nx) > Math.abs(ny) ? (nx > 0 ? "right" : "left") : (ny > 0 ? "front" : "back");
}

// The hours of direct sun each square of a bed gets a day in the middle of a month, worked out if need be: null
// until they are.
/** @param {Bed} bed @param {number} month */
function sunHours(bed, month) {
  const sun = state.planner.sun.get(bed.name);
  return sun && sun !== "working" ? sun[GARDEN_MONTHS.indexOf(month)] : null;
}

// The sun a planting gets, on average over its squares and the months it grows; null if it isn't worked out.
/** @param {Planting} p */
function sunAt(p) {
  const bed = currentBed();
  const plant = plantOf(p.plant);
  const sun = bed && state.planner.sun.get(bed.name);
  if (!bed || !sun || sun === "working") return null;
  const months = growingMonths(plant);
  let total = 0;
  for (const m of months) {
    const hours = sun[GARDEN_MONTHS.indexOf(m)];
    for (let dx = 0; dx < plant.size; dx++) for (let dy = 0; dy < plant.size; dy++) total += hours[(p.y + dy) * bed.w + p.x + dx];
  }
  return total / months.length / plant.size ** 2;
}

// The months, of April to October, a plant is in the bed, from its planting to its last harvest; else June to August.
/** @param {Plant} p */
function growingMonths(p) {
  const site = state.gardenSite;
  if (site && !p.perennial) {
    const w = plantWindows(p, site, seasonYear(site));
    const from = (w.plant ?? w.fall)?.[0];
    const to = w.harvest[0]?.[1];
    if (from && to && to.getFullYear() === from.getFullYear()) {
      const months = GARDEN_MONTHS.filter((m) => m >= from.getMonth() && m <= to.getMonth());
      if (months.length) return months;
    }
  }
  return p.perennial ? [4, 5, 6, 7, 8] : [5, 6, 7];
}

// The sun worked out for the bed shown, if it isn't yet: the scene built in each season, its trees in leaf or bare,
// and from just over each square's soil, a ray toward the sun every GARDEN_STEP hours of each month's middle day,
// counted as sun unless it hits something. A step at a time, so the page keeps going.
async function ensureSun() {
  const planner = state.planner;
  const bed = currentBed();
  const site = state.gardenSite;
  if (!bed || !site || !state.details) return;
  const key = houseKey();
  if (planner.sunFor !== key) {
    planner.sun = new Map();
    planner.casters = new Map();
    planner.sunFor = key;
  }
  if (planner.sun.has(bed.name)) return;
  planner.sun.set(bed.name, "working");
  drawBed();
  await new Promise((resolve) => setTimeout(resolve, 30));
  const scene = /** @type {Scene | NoScene} */ (state.modelScene ??= pageModelScene());
  if (scene.note) {
    planner.sun.delete(bed.name);
    return;
  }
  const points = [];
  for (let y = 0; y < bed.d; y++) {
    for (let x = 0; x < bed.w; x++) {
      const [px, py] = [bed.ground.x + x + 0.5, bed.ground.y + y + 0.5];
      points.push([px, py, groundTop(scene.details, px, py) + 0.5]);
    }
  }
  const latitude = (site.latitude * Math.PI) / 180;
  /** @type {Float32Array[]} */
  const hours = GARDEN_MONTHS.map(() => new Float32Array(points.length));
  /** @type {[Season, number[]][]} */
  const seasons = [["spring", [3, 4]], ["summer", [5, 6, 7]], ["fall", [8, 9]]];
  const pause = () => new Promise((resolve) => setTimeout(resolve, 0));
  for (const [season, months] of seasons) {
    // Each season's scene and its grid kept for the next bed, which then only casts its rays.
    let blocked = planner.casters.get(season);
    if (!blocked) {
      await pause();
      if (planner.sunFor !== key) return; // the house changed meanwhile; it's worked out again for the new one
      const { data } = buildModel(scene, "outside", [Math.SQRT1_2, Math.SQRT1_2], { trees: true, season });
      await pause();
      if (planner.sunFor !== key) return;
      blocked = rayCaster(data);
      planner.casters.set(season, blocked);
    }
    for (const month of months) {
      await pause();
      if (planner.sunFor !== key) return;
      const day = Math.round((Date.UTC(2001, month, 15) - Date.UTC(2001, 0, 0)) / DAY);
      const declination = ((23.44 * Math.PI) / 180) * Math.sin((2 * Math.PI * (284 + day)) / 365);
      const out = hours[GARDEN_MONTHS.indexOf(month)];
      for (let t = -12 + GARDEN_STEP / 2; t < 12; t += GARDEN_STEP) {
        const dir = sunDirection(latitude, declination, (t * Math.PI) / 12, site.facing);
        if (!dir) continue;
        points.forEach((p, k) => {
          if (!blocked(p, dir)) out[k] += GARDEN_STEP;
        });
      }
    }
  }
  planner.sun.set(bed.name, hours);
  if (planner.bed === bed.name) {
    drawBed();
    renderCard();
    renderNotes();
  }
  ensureSun(); // the bed shown now, if it's another
}

// The sun's direction on the plan, x, y and z, at an hour angle from solar noon (radians), or null when it's down:
// its height and bearing from the latitude and the sun's declination, the bearing turned to the plan by the way its
// front faces.
/** @param {number} latitude @param {number} declination @param {number} hour @param {number} facing */
function sunDirection(latitude, declination, hour, facing) {
  const up = Math.sin(latitude) * Math.sin(declination) + Math.cos(latitude) * Math.cos(declination) * Math.cos(hour);
  if (up < 0.02) return null; // within about a degree of the horizon
  const across = Math.sqrt(1 - up * up);
  const bearing = Math.atan2(Math.sin(hour), Math.cos(hour) * Math.sin(latitude) - Math.tan(declination) * Math.cos(latitude)) + Math.PI;
  const angle = bearing - (facing * Math.PI) / 180 + Math.PI / 2;
  return [Math.cos(angle) * across, Math.sin(angle) * across, up];
}

// Whether a ray from a point toward a direction hits any of a mesh's triangles: the triangles sorted into a grid of
// cells CELL ft across, and the ray walked through it cell by cell (3D DDA), each triangle in a cell tried once.
/** @param {Float32Array} data @returns {(o: number[], dir: number[]) => boolean} */
function rayCaster(data) {
  const CELL = 2;
  const n = data.length / 30;
  const T = new Float64Array(n * 9);
  const lo = [Infinity, Infinity, Infinity];
  const hi = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < n; i++) {
    for (let v = 0; v < 3; v++) {
      for (let k = 0; k < 3; k++) {
        const c = data[i * 30 + v * 10 + k];
        lo[k] = Math.min(lo[k], c);
        hi[k] = Math.max(hi[k], c);
      }
    }
  }
  const dims = [0, 1, 2].map((k) => Math.max(1, Math.ceil((hi[k] - lo[k] + 2) / CELL)));
  /** @param {number} v @param {number} k */
  const cellOf = (v, k) => Math.min(dims[k] - 1, Math.max(0, Math.floor((v - lo[k] + 1) / CELL)));
  /** @type {Map<number, number[]>} */
  const grid = new Map();
  for (let i = 0; i < n; i++) {
    const o = i * 30;
    const p = [0, 1, 2].map((v) => [data[o + v * 10], data[o + v * 10 + 1], data[o + v * 10 + 2]]);
    T.set([...p[0], p[1][0] - p[0][0], p[1][1] - p[0][1], p[1][2] - p[0][2], p[2][0] - p[0][0], p[2][1] - p[0][1], p[2][2] - p[0][2]], i * 9);
    const [a, b] = [[0, 1, 2].map((k) => cellOf(Math.min(p[0][k], p[1][k], p[2][k]), k)), [0, 1, 2].map((k) => cellOf(Math.max(p[0][k], p[1][k], p[2][k]), k))];
    for (let x = a[0]; x <= b[0]; x++) {
      for (let y = a[1]; y <= b[1]; y++) {
        for (let z = a[2]; z <= b[2]; z++) {
          const key = (x * dims[1] + y) * dims[2] + z;
          const list = grid.get(key);
          if (list) list.push(i);
          else grid.set(key, [i]);
        }
      }
    }
  }
  const seen = new Int32Array(n);
  let stamp = 0;
  // Möller–Trumbore: whether the ray from o along dir meets triangle i ahead of it.
  /** @param {number[]} o @param {number[]} d @param {number} i */
  const hits = (o, d, i) => {
    const k = i * 9;
    const [e1x, e1y, e1z, e2x, e2y, e2z] = [T[k + 3], T[k + 4], T[k + 5], T[k + 6], T[k + 7], T[k + 8]];
    const [hx, hy, hz] = [d[1] * e2z - d[2] * e2y, d[2] * e2x - d[0] * e2z, d[0] * e2y - d[1] * e2x];
    const a = e1x * hx + e1y * hy + e1z * hz;
    if (Math.abs(a) < 1e-12) return false;
    const f = 1 / a;
    const [sx, sy, sz] = [o[0] - T[k], o[1] - T[k + 1], o[2] - T[k + 2]];
    const u = f * (sx * hx + sy * hy + sz * hz);
    if (u < 0 || u > 1) return false;
    const [qx, qy, qz] = [sy * e1z - sz * e1y, sz * e1x - sx * e1z, sx * e1y - sy * e1x];
    const v = f * (d[0] * qx + d[1] * qy + d[2] * qz);
    if (v < 0 || u + v > 1) return false;
    return f * (e2x * qx + e2y * qy + e2z * qz) > 1e-4;
  };
  return (o, d) => {
    stamp++;
    const c = [0, 1, 2].map((k) => cellOf(o[k], k));
    const step = d.map((v) => (v > 0 ? 1 : -1));
    const tMax = [0, 1, 2].map((k) => (d[k] === 0 ? Infinity : (lo[k] - 1 + (c[k] + (d[k] > 0 ? 1 : 0)) * CELL - o[k]) / d[k]));
    const tDelta = d.map((v) => (v === 0 ? Infinity : CELL / Math.abs(v)));
    for (;;) {
      const list = grid.get((c[0] * dims[1] + c[1]) * dims[2] + c[2]);
      if (list) {
        for (const i of list) {
          if (seen[i] === stamp) continue;
          seen[i] = stamp;
          if (hits(o, d, i)) return true;
        }
      }
      const k = tMax[0] < tMax[1] ? (tMax[0] < tMax[2] ? 0 : 2) : tMax[1] < tMax[2] ? 1 : 2;
      c[k] += step[k];
      if (c[k] < 0 || c[k] >= dims[k]) return false;
      tMax[k] += tDelta[k];
    }
  };
}

// --- Dates: the season's frost dates, and when each plant is started, planted and harvested

function today() {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate());
}

/** @param {Date} date @param {number} days */
function addDays(date, days) {
  const moved = new Date(date);
  moved.setDate(moved.getDate() + Math.round(days));
  return moved;
}

/** @param {Garden} site @param {number} year @returns {[Date, Date]} */
function frostDates(site, year) {
  return [new Date(year, site.last_frost[0] - 1, site.last_frost[1]), new Date(year, site.first_frost[0] - 1, site.first_frost[1])];
}

// The season being planned: this year's, until a month after its first frost, and then the next.
/** @param {Garden} site */
function seasonYear(site) {
  const now = today();
  return now > addDays(frostDates(site, now.getFullYear())[1], 28) ? now.getFullYear() + 1 : now.getFullYear();
}

// When a plant is started indoors, planted out and sown for fall in a year's season, and harvested after each
// planting: from its earliest planting and quickest days to its latest and slowest, plus the weeks it picks, but
// ended by frost as far as it takes it. One that isn't ready before then winters over and is harvested the next year,
// as garlic is.
/** @param {Plant} p @param {Garden} site @param {number} year @returns {Windows} */
function plantWindows(p, site, year) {
  const [last, first] = frostDates(site, year);
  /** @param {Date} base @param {number[]} weeks @returns {Span} */
  const span = (base, [a, b]) => [addDays(base, a * 7), addDays(base, b * 7)];
  const start = p.start && span(last, p.start);
  const plant = p.plant && span(last, p.plant);
  const fall = p.fall && span(first, p.fall);
  const end = addDays(first, GARDEN_FROST_END[p.frost]);
  /** @param {Span} sown @returns {Span | null} */
  const harvest = ([a, b]) => {
    const from = addDays(a, p.days[0]);
    const to = addDays(b, p.days[1] + p.harvest * 7);
    const capped = from < end && to > end ? end : to;
    return from < capped ? [from, capped] : null;
  };
  const harvests = /** @type {Span[]} */ ([plant, fall].flatMap((s) => (s ? [harvest(s)] : [])).filter((h) => h));
  return { start, plant, fall, harvest: harvests };
}

/** @param {Date} date */
function dateText(date) {
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** @param {Span} span @param {boolean} [year] */
function spanText([a, b], year = false) {
  const next = year && state.gardenSite && a.getFullYear() > seasonYear(state.gardenSite) ? " the next year" : "";
  return `${dateText(a)} – ${dateText(b)}`.replaceAll(" ", "\u00a0") + next; // kept together on one line
}

/** @param {number} weeks */
function weeksText(weeks) {
  if (weeks === 0) return "on";
  const n = Math.abs(weeks);
  return `${n} week${n === 1 ? "" : "s"} ${weeks < 0 ? "before" : "after"}`;
}

/** @param {number[]} months */
function monthsText(months) {
  return months.length === 1 ? MONTH_NAMES[months[0]] : `${MONTH_NAMES[months[0]]}–${MONTH_NAMES[/** @type {number} */ (months.at(-1))]}`;
}

/** @param {number} value @param {boolean} [exact] */
function inches(value, exact = false) {
  if (exact) return `${value < 1 ? value.toFixed(2) : value.toFixed(1)} in`;
  const fractions = { 0.25: "¼", 0.5: "½", 0.75: "¾" };
  const whole = Math.floor(value);
  const part = fractions[/** @type {keyof typeof fractions} */ (Math.round((value - whole) * 4) / 4)] ?? "";
  return `${whole || !part ? whole : ""}${part} in`;
}

/** @param {number} height */
function feetInches(height) {
  return height < 24 ? `${height} in` : `${Math.round(height / 6) / 2} ft`;
}

/** @param {string[]} items */
function listText(items) {
  return items.length < 2 ? items.join("") : `${items.slice(0, -1).join(", ")} and ${items.at(-1)}`;
}

// --- The calendar: each plant in the bed, its windows across the year

const CALENDAR_TOP = 22; // px above the first row, for the months
const CALENDAR_ROW = 26;
const CALENDAR_NAME = 110; // px for the plants' names

// The plants the calendar shows: those in the bed, in order of their first start or planting, and the brush's if
// it's not among them.
function calendarRows() {
  const planner = state.planner;
  const site = state.gardenSite;
  if (!site || !planner.plants.size) return [];
  const ids = [...new Set(plantingsOf().map((p) => p.plant))];
  if (planner.brush && planner.brush !== "remove" && !ids.includes(planner.brush)) ids.push(planner.brush);
  const year = seasonYear(site);
  const first = (/** @type {string} */ id) => {
    const w = plantWindows(plantOf(id), site, year);
    return Math.min(...[w.start, w.plant, w.fall].flatMap((s) => (s ? [s[0].getTime()] : [])));
  };
  return ids.map((id) => ({ plant: plantOf(id), windows: plantWindows(plantOf(id), site, year) })).sort((a, b) => first(a.plant.id) - first(b.plant.id));
}

function drawCalendar() {
  const planner = state.planner;
  const site = state.gardenSite;
  const root = planner.calendarSvg;
  root.replaceChildren();
  const rows = calendarRows();
  planner.calendar.hidden = !site || !currentBed();
  if (!site) return;
  const year = seasonYear(site);
  const width = Math.max(320, root.parentElement?.clientWidth ?? 600);
  const plot = width - CALENDAR_NAME - 8;
  const height = CALENDAR_TOP + Math.max(1, rows.length) * CALENDAR_ROW + 4;
  root.setAttribute("width", String(width));
  root.setAttribute("height", String(height));
  root.setAttribute("viewBox", `0 0 ${width} ${height}`);
  root.setAttribute("aria-label", `When to start, plant and harvest what's in the bed, across ${year}. Arrow keys step through the plants.`);
  const start = new Date(year, 0, 1).getTime();
  const span = new Date(year + 1, 0, 1).getTime() - start;
  // Where a day falls across the year, the next year's as the same day this year, as a harvest that winters over.
  /** @param {Date} d */
  const xOf = (d) => CALENDAR_NAME + (((d.getTime() - start) % span + span) % span / span) * plot;
  for (let m = 0; m < 12; m++) {
    const x = CALENDAR_NAME + ((new Date(year, m, 1).getTime() - start) / span) * plot;
    root.append(svg("line", { x1: x, x2: x, y1: CALENDAR_TOP - 4, y2: height - 4, class: "month-line" }));
    const label = svg("text", { x: x + 3, y: 14, class: "tick" });
    label.textContent = plot / 12 > 28 ? MONTH_NAMES[m] : MONTH_NAMES[m][0];
    root.append(label);
  }
  const [last, first] = frostDates(site, year);
  for (const [day, name] of [[last, "Last frost"], [first, "First frost"]]) {
    const line = svg("line", { x1: xOf(/** @type {Date} */ (day)), x2: xOf(/** @type {Date} */ (day)), y1: CALENDAR_TOP - 4, y2: height - 4, class: "frost-line" });
    const title = svg("title");
    title.textContent = `${name}, on average: ${dateText(/** @type {Date} */ (day))}`;
    line.append(title);
    root.append(line);
  }
  if (!rows.length) {
    const empty = svg("text", { x: CALENDAR_NAME, y: CALENDAR_TOP + 16, class: "empty" });
    empty.textContent = "Plant something, or pick a plant, to see when to start and harvest it.";
    root.append(empty);
  }
  rows.forEach(({ plant, windows }, k) => {
    const y = CALENDAR_TOP + k * CALENDAR_ROW;
    const g = svg("g", { class: `calendar-row${planner.row === k ? " lit" : ""}` });
    const name = svg("text", { x: 0, y: y + 16, class: "name" });
    name.textContent = plant.name;
    g.append(svg("rect", { x: 0, y, width, height: CALENDAR_ROW, class: "row-band" }), name);
    /** @param {Span} s @param {string} cls @param {number} top @param {number} tall */
    const bar = ([a, b], cls, top, tall) => {
      const [x0, x1] = [xOf(a), xOf(b)];
      const pieces = x1 >= x0 ? [[x0, x1]] : [[x0, CALENDAR_NAME + plot], [CALENDAR_NAME, x1]]; // past the year's end, on from its start
      for (const [p, q] of pieces) g.append(svg("rect", { x: p, y: y + top, width: Math.max(3, q - p), height: tall, rx: 2, class: cls }));
    };
    if (windows.start) bar(windows.start, "bar-start", 9, 8);
    for (const h of windows.harvest) bar(h, "bar-harvest", 5, 16);
    if (windows.plant) bar(windows.plant, "bar-plant", 7, 12);
    if (windows.fall) bar(windows.fall, "bar-plant", 7, 12);
    root.append(g);
  });
  const now = today();
  if (now.getFullYear() === year) {
    const x = xOf(now);
    root.append(svg("line", { x1: x, x2: x, y1: CALENDAR_TOP - 6, y2: height - 2, class: "today" }));
  }
  const key = /** @type {HTMLElement} */ (planner.calendar.querySelector(".garden-calendar-key"));
  key.replaceChildren(el("ul", { class: "coverage-key" },
    el("li", {}, el("span", { class: "swatch bar-start" }), "Start indoors"),
    el("li", {}, el("span", { class: "swatch bar-plant" }), "Plant or sow"),
    el("li", {}, el("span", { class: "swatch bar-harvest" }), "Harvest or bloom"),
    el("li", {}, el("span", { class: "swatch frost-line" }), `Average frosts, ${dateText(last)} and ${dateText(first)}`),
    ...(now.getFullYear() === year ? [el("li", {}, el("span", { class: "swatch today" }), "Today")] : [el("li", { class: "coverage-unit" }, `The ${year} season`)])));
  renderCalendarReadout();
  renderCalendarTable(rows);
  renderUpcoming(rows);
}

/** @param {number | null} k */
function setCalendarRow(k) {
  if (state.planner.row === k) return;
  state.planner.row = k;
  drawCalendar();
}

function renderCalendarReadout() {
  const planner = state.planner;
  const rows = calendarRows();
  const row = planner.row != null ? rows[planner.row] : null;
  planner.readout.hidden = !row;
  if (!row) return;
  const { plant, windows } = row;
  const lines = [
    ...(windows.start ? [`Start indoors ${spanText(windows.start)}`] : []),
    ...(windows.plant ? [`${plantVerb(plant)} ${spanText(windows.plant)}`] : []),
    ...(windows.fall ? [`${plant.plant ? "For fall" : plantVerb(plant)} ${spanText(windows.fall)}`] : []),
    ...windows.harvest.map((h) => `${plant.food ? "Harvest" : "Blooms"} ${spanText(h, true)}`),
  ];
  planner.readout.replaceChildren(el("div", { class: "head" }, el("strong", {}, plant.name)), ...lines.map((line) => el("div", { class: "row" }, line)));
  planner.readout.style.top = `${CALENDAR_TOP + (/** @type {number} */ (planner.row) + 1) * CALENDAR_ROW + 4}px`;
}

/** @param {ReturnType<typeof calendarRows>} rows */
function renderCalendarTable(rows) {
  const head = el("tr", {}, ...["Plant", "Start indoors", "Plant or sow", "For fall", "Harvest or bloom"].map((h) => el("th", { scope: "col" }, h)));
  const body = rows.map(({ plant, windows }) => el("tr", {},
    el("td", {}, plant.name),
    el("td", {}, windows.start ? spanText(windows.start) : "—"),
    el("td", {}, windows.plant ? spanText(windows.plant) : "—"),
    el("td", {}, windows.fall ? spanText(windows.fall) : "—"),
    el("td", {}, windows.harvest.map((h) => spanText(h, true)).join("; ") || "—")));
  state.planner.table.replaceChildren(el("table", {}, el("thead", {}, head), el("tbody", {}, ...body)));
}

// What's next for the plants in the bed: each start, planting and harvest still to come, the soonest first.
/** @param {ReturnType<typeof calendarRows>} rows */
function renderUpcoming(rows) {
  const planner = state.planner;
  const now = today();
  /** @type {{from: Date, to: Date, text: string}[]} */
  const items = [];
  for (const { plant, windows } of rows) {
    const name = plant.name.toLowerCase();
    if (windows.start) items.push({ from: windows.start[0], to: windows.start[1], text: `Start ${name} indoors` });
    if (windows.plant) items.push({ from: windows.plant[0], to: windows.plant[1], text: `${plantVerb(plant)} ${name}` });
    if (windows.fall) items.push({ from: windows.fall[0], to: windows.fall[1], text: plant.plant ? `${plantVerb(plant)} ${name} for a fall crop` : `${plantVerb(plant)} ${name}` });
    for (const h of windows.harvest) items.push({ from: h[0], to: h[1], text: `${plant.food ? "Harvest" : "Flowers on"} ${name}` });
  }
  const coming = items.filter((i) => i.to >= now).sort((a, b) => a.from.getTime() - b.from.getTime()).slice(0, 8);
  planner.upcoming.replaceChildren(...coming.map((i) => el("li", {},
    el("span", { class: "when" }, i.from <= now ? `Now – ${dateText(i.to)}` : `${dateText(i.from)} – ${dateText(i.to)}`), i.text)));
  if (!coming.length) planner.upcoming.append(el("li", { class: "empty" }, rows.length ? "Nothing more this season." : "Nothing planted yet."));
}

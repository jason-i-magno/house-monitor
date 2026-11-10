"use strict";

// How long a room can go without a reading before its card says so. Real
// sensors report about once a minute, so this allows a few missed broadcasts.
const STALE_AFTER = 5 * 60;
const RANGES = [
  { label: "6 h", hours: 6 },
  { label: "24 h", hours: 24 },
  { label: "7 d", hours: 24 * 7 },
  { label: "30 d", hours: 24 * 30 },
];
const TEMPERATURE = { unit: "°F", index: 1, value: (c) => c * 9 / 5 + 32, digits: 1, minSpan: 4 };
const HUMIDITY = { unit: "%", index: 2, value: (h) => h, digits: 0, minSpan: 10 };
// Each chart's measure and rooms. Unheated spaces, like the garage, and the
// outside get their own temperature chart, since their range would flatten the
// rooms' lines.
// A chart with no rooms is hidden. A room whose sensor reads only temperature,
// its latest reading with no humidity, is left out of the humidity chart.
const CHARTS = [
  { title: "Temperature", metric: TEMPERATURE, rooms: (sensor) => sensor.heated },
  { title: "Temperature outside and in unheated spaces", metric: TEMPERATURE, rooms: (sensor) => !sensor.heated },
  { title: "Humidity", metric: HUMIDITY, rooms: (sensor) => sensor.reading?.humidity !== null },
];
const SERIES_SLOTS = 8; // rooms past the eighth share a gray rather than reuse a color
// The gray rooms are told apart by dash pattern, solid first. Each is the
// visible lengths of dash and gap, a dash of 0 being a dot: solid, dashed,
// dotted, dash-dot, dash-dot-dot and long dashes, each short enough to show in
// a 20px line key. Past six, they repeat.
const OTHER_DASHES = [[], [5, 3], [0, 3], [7, 3, 0, 3], [5, 2.4, 0, 2.4, 0, 2.4], [9, 3]];
const SVG_NS = "http://www.w3.org/2000/svg";
const HIDDEN_ROOMS_KEY = "house-monitor-hidden-rooms"; // {chart title: [sensor id]}, for this viewer
const CHART_HEIGHT = 260;
// The house map colors each room by temperature, in °F, on a gradient through
// the --temp-0 to --temp-6 colors placed at these temperatures: a comfortable
// gray near the thermostat's 68 °F, shading to blue colder and red warmer,
// and holding past either end.
const TEMP_STOPS = [59.5, 62.5, 65.5, 68.5, 71.5, 74.5, 77.5];
const TEMP_TICKS = [60, 64, 68, 72, 76];
// The two inks for a label on a room's color, sRGB from 0 to 1: white and near-black.
const INKS = { "#ffffff": [1, 1, 1], "#0b0b0b": [11 / 255, 11 / 255, 11 / 255] };
const MAP_VIEWS = [
  { id: "plan", label: "Floor plan" },
  { id: "3d", label: "3D" },
  { id: "model", label: "Detailed" }, // in model.js
];
const ROOM_HEIGHT = 8; // feet, floor to ceiling, for rooms without a plan, as in Plan
const MAP_HOME = { angle: 35, tilt: 30 }; // the 3D view's first angle, and where Reset goes back to
const MAP_KEY_TURN = 15; // degrees the 3D view turns or tilts for a press of shift and an arrow key
const MAP_KEY_ZOOM = 1.25; // times the detailed view zooms in, or out, for a press of plus or minus
// Where to try a room's label on a face of its box, as fractions across it: the middle first.
const LABEL_SPOTS = [[0.5, 0.5], [0.5, 0.25], [0.5, 0.75], [0.25, 0.5], [0.75, 0.5], [0.25, 0.25], [0.75, 0.25], [0.25, 0.75], [0.75, 0.75]];

const state = {
  // [{id, name, floor, plan, latest, reading, card}], in house order; a plan is a list of pieces for an L. latest is
  // the newest reading, and reading the one shown: null while it's the simulator's and those are hidden.
  sensors: [],
  hideSimulated: false, // the simulator's readings left out, for this viewer
  areas: [], // [{name, floor, plan}]: parts of the house with no sensor
  details: null, // the doors, windows, roof, yard and more for the detailed view (housemon/details.py), if any
  wall: null, // how thick every wall is, in feet (housemon/layout.py)
  hours: 24,
  history: null, // {since, until, bucket, series: {sensor_id: [[ts, temperature_c, humidity], ...]}}
  charts: [],
  historyRequest: 0,
  map: null, // the house map's elements, and the layout it last drew
  mapView: "plan",
  mapAngle: MAP_HOME.angle, // degrees the 3D view is turned, from looking at the front
  mapFull: false, // the map filling the screen
  mapTilt: MAP_HOME.tilt, // degrees the 3D view looks down from level, -90 (from below) to 90 (from above)
  mapFocus: null, // the sensor id the map's readout shows, or in the detailed view any part's key
  modelCut: "outside", // the detailed view's whole house, or the level it's cut away to
  modelTrees: true, // whether the detailed view's whole house shows its trees
  modelSeason: seasonOf(new Date()), // the season they're shown in, today's when the page loads
  modelCoverage: false, // the detailed view's lawn and beds shaded by how many sprinkler heads reach them
  modelCoverageBy: "heads", // or by how much water they put on each spot, "water"
  modelScene: null, // what the detailed view draws, built from the sensors and details when first needed
  modelZoom: null, // how far the detailed view is zoomed in, 1 showing the whole lot, or null to frame the house
  modelTarget: null, // the point in feet the zoomed-in view centers on, or null for the middle
  tempStops: null, // the scale's colors in OKLab, read from the theme when first needed
};

// --- DOM helpers ---------------------------------------------------------

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, value);
  node.append(...children);
  return node;
}

function svg(tag, attrs = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, value);
  return node;
}

function seriesColor(i) {
  return i < SERIES_SLOTS ? `var(--series-${i + 1})` : "var(--series-other)";
}

// The stroke-dasharray for room i's line at this width. Round caps add half
// the width to each end of a dash, so dashes are drawn that much shorter and
// gaps that much longer.
function seriesDash(i, width) {
  const pattern = i < SERIES_SLOTS ? [] : OTHER_DASHES[(i - SERIES_SLOTS) % OTHER_DASHES.length];
  if (!pattern.length) return "none";
  return pattern.map((v, k) => (k % 2 ? v + width : Math.max(v - width, 0.01))).join(" ");
}

function lineKey(i) {
  const key = svg("svg", { class: "key", width: 20, height: 3, "aria-hidden": "true" });
  key.append(svg("line", {
    x1: 1.5,
    y1: 1.5,
    x2: 18.5,
    y2: 1.5,
    stroke: seriesColor(i),
    "stroke-width": 3,
    "stroke-linecap": "round",
    "stroke-dasharray": seriesDash(i, 3),
  }));
  return key;
}

function fahrenheit(celsius) {
  return celsius * 9 / 5 + 32;
}

function ago(seconds) {
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))} s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return `${Math.floor(seconds / 86400)} d ago`;
}

function duration(seconds) {
  return ago(seconds).replace(" ago", "");
}

// --- Room cards ----------------------------------------------------------

// Whether a room is hidden: its newest reading made up by the simulator, while those are hidden. A room read from
// a sensor stays, its history without what the simulator made up before.
function hiddenRoom(sensor) {
  return state.hideSimulated && Boolean(sensor.latest?.simulated);
}

function showReading(sensor) {
  sensor.reading = hiddenRoom(sensor) ? null : sensor.latest ?? null;
}

// Hide or show the simulator's readings: their rooms' cards, values on the map, lines and history in the charts.
function setHideSimulated(hide) {
  state.hideSimulated = hide;
  try {
    localStorage.setItem("house-monitor-hide-simulated", String(hide));
  } catch {}
  renderSimulatedToggle();
  state.sensors.forEach(showReading);
  updateRooms();
  state.charts.forEach(renderLegend);
  drawMap();
  loadHistory();
}

function renderSimulatedToggle() {
  const button = document.getElementById("simulated");
  button.setAttribute("aria-pressed", String(state.hideSimulated));
  button.onclick = () => setHideSimulated(!state.hideSimulated);
}

function renderRooms() {
  const root = document.getElementById("rooms");
  root.replaceChildren();
  for (const floor of new Set(state.sensors.map((s) => s.floor))) {
    const grid = el("div", { class: "room-grid" });
    state.sensors.forEach((sensor, i) => {
      if (sensor.floor === floor) grid.append(roomCard(sensor, i));
    });
    const outside = state.sensors.every((s) => s.floor !== floor || s.outside); // not a level
    root.append(el("section", { class: "floor" }, el("h2", {}, outside ? floor : `${floor} level`), grid));
  }
  updateRooms();
}

function roomCard(sensor, i) {
  sensor.card = el(
    "article",
    { class: "room" },
    el("h3", {}, lineKey(i), sensor.name),
    el("p", { class: "temp" }, el("span", { class: "value" }, "–"), el("span", { class: "unit" }, "°F")),
    el("p", { class: "humidity" }),
    el("p", { class: "meta updated-at" }),
    el("p", { class: "meta device" }),
    el("p", { class: "stale", hidden: "" }, el("span", { class: "icon", "aria-hidden": "true" }, "⚠"), el("span")),
  );
  return sensor.card;
}

function updateRooms() {
  const now = Date.now() / 1000;
  for (const sensor of state.sensors) {
    const card = sensor.card;
    const reading = sensor.reading;
    card.hidden = hiddenRoom(sensor);
    const [value, humidity, updatedAt, device, stale] = [".value", ".humidity", ".updated-at", ".device", ".stale"]
      .map((s) => card.querySelector(s));
    if (!reading) {
      humidity.textContent = "No readings yet";
      continue;
    }
    const age = now - reading.ts;
    value.textContent = fahrenheit(reading.temperature_c).toFixed(1);
    humidity.textContent = reading.humidity == null ? "Reads temperature only" : `${Math.round(reading.humidity)}% humidity`;
    updatedAt.textContent = `Updated ${ago(age)}`;
    // Each detail stays whole, so a narrow card wraps between them. A made-up reading says so, in place of a
    // battery and signal that are made up too.
    const details = [];
    if (reading.simulated) details.push("Simulated");
    else if (reading.battery != null) details.push(`Battery ${reading.battery}%`);
    if (!reading.simulated && reading.rssi != null) details.push(`Signal ${String(reading.rssi).replace("-", "−")} dBm`);
    device.replaceChildren(...details.flatMap((text, i) => [i ? " · " : "", el("span", { class: "nowrap" }, text)]));
    const isStale = age > STALE_AFTER;
    card.classList.toggle("is-stale", isStale);
    stale.hidden = !isStale;
    stale.lastElementChild.textContent = `No reading for ${duration(age)}`;
  }
  // A level whose rooms are all hidden goes too, and the toggle shows only while there's something to hide.
  for (const section of document.querySelectorAll("#rooms .floor")) {
    section.hidden = [...section.querySelectorAll(".room")].every((card) => card.hidden);
  }
  document.getElementById("simulated").hidden = !state.sensors.some((s) => s.latest?.simulated);
}

// --- Charts --------------------------------------------------------------

function createChart({ title, metric, rooms }) {
  const plot = el("div", { class: "plot" });
  const tooltip = el("div", { class: "tooltip", hidden: "" });
  const legend = el("ul", { class: "legend", "aria-label": "Rooms. Press one to hide or show its line." });
  const tableWrap = el("div", { class: "table-wrap" });
  const details = el("details", { class: "table-view" }, el("summary", {}, "Show as table"), tableWrap);
  const figure = el(
    "figure",
    { class: "chart" },
    el("figcaption", {}, el("h2", {}, title), el("span", { class: "unit" }, metric.unit)),
    legend,
    plot,
    details,
  );
  const root = svg("svg", {
    height: CHART_HEIGHT,
    tabindex: 0,
    role: "img",
    "aria-label": `${title} by room. Arrow keys step through the readings; the table below lists them all.`,
  });
  plot.append(root, tooltip);
  let hidden = [];
  try {
    hidden = JSON.parse(localStorage.getItem(HIDDEN_ROOMS_KEY))?.[title] ?? [];
  } catch {}
  const chart = {
    title, metric, figure, svg: root, plot, tooltip, legend, details, tableWrap,
    rooms: (sensor) => rooms(sensor) && !hiddenRoom(sensor), // but rooms whose made-up readings are hidden
    hidden: new Set(hidden), // the sensor ids whose lines are hidden
    layout: null,
    hoverTime: null,
  };
  root.addEventListener("focus", () => {
    if (chart.layout) setHover(chart, chart.hoverTime ?? snap(chart.layout, chart.layout.until));
  });
  root.addEventListener("blur", () => setHover(chart, null));
  root.addEventListener("keydown", (event) => keyHover(chart, event));
  details.addEventListener("toggle", () => details.open && renderTable(chart));
  plot.addEventListener("pointermove", (event) => pointerHover(chart, event));
  plot.addEventListener("pointerdown", (event) => pointerHover(chart, event)); // a tap on a phone
  plot.addEventListener("pointerleave", () => {
    if (document.activeElement !== chart.svg) setHover(chart, null);
  });
  return chart;
}

// Whether a room's line is drawn in a chart: the chart has it, and it isn't hidden.
function shown(chart, sensor) {
  return chart.rooms(sensor) && !chart.hidden.has(sensor.id);
}

// Each room keeps its house-wide color, whichever charts it's in. Its legend
// entry is a button that hides or shows its line, and the last button hides
// or shows them all, so one room can be followed alone.
function renderLegend(chart) {
  chart.figure.hidden = !state.sensors.some(chart.rooms);
  const entries = state.sensors.flatMap((sensor, i) => {
    if (!chart.rooms(sensor)) return [];
    const button = el("button", { type: "button", "data-sensor": sensor.id }, lineKey(i), sensor.name);
    button.addEventListener("click", () => hideRooms(chart, [sensor.id], shown(chart, sensor)));
    return [el("li", {}, button)];
  });
  const all = el("button", { type: "button", class: "all" });
  all.addEventListener("click", () => {
    const ids = state.sensors.filter(chart.rooms).map((s) => s.id);
    hideRooms(chart, ids, ids.every((id) => !chart.hidden.has(id)));
  });
  chart.legend.replaceChildren(...entries, el("li", {}, all));
  updateLegend(chart);
}

// Updated in place, so a button keeps its focus.
function updateLegend(chart) {
  for (const button of chart.legend.querySelectorAll("button[data-sensor]")) {
    button.setAttribute("aria-pressed", String(!chart.hidden.has(button.dataset.sensor)));
  }
  const anyHidden = state.sensors.some((s) => chart.rooms(s) && chart.hidden.has(s.id));
  chart.legend.querySelector(".all").textContent = anyHidden ? "Show all" : "Hide all";
}

function hideRooms(chart, ids, hide) {
  for (const id of ids) {
    if (hide) chart.hidden.add(id);
    else chart.hidden.delete(id);
  }
  try {
    localStorage.setItem(HIDDEN_ROOMS_KEY, JSON.stringify(Object.fromEntries(state.charts.map((c) => [c.title, [...c.hidden]]))));
  } catch {}
  updateLegend(chart);
  drawChart(chart);
  if (chart.details.open) renderTable(chart);
}

// A clean axis covering [min, max], at least minSpan wide: [low, high, step].
function niceScale(min, max, minSpan) {
  if (max - min < minSpan) {
    const middle = (min + max) / 2;
    [min, max] = [middle - minSpan / 2, middle + minSpan / 2];
  }
  const rough = (max - min) / 4;
  const power = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * power).find((s) => s >= rough);
  return [Math.floor(min / step) * step, Math.ceil(max / step) * step, step];
}

// Time ticks on local clock boundaries, as many as fit about 64px apart.
// Midnight ticks show the date; weekly ticks fall on Mondays.
function timeTicks(since, until, plotWidth) {
  const spanHours = (until - since) / 3600;
  const stepHours = [1, 2, 3, 6, 12, 24, 48, 168].find((h) => spanHours / h <= plotWidth / 64) ?? 168;
  const onStep = (d) => {
    if (stepHours < 24) return d.getHours() % stepHours === 0;
    if (d.getHours() !== 0) return false;
    return stepHours === 24 || (stepHours === 48 ? d.getDate() % 2 === 1 : d.getDay() === 1);
  };
  const ticks = [];
  const d = new Date(since * 1000);
  d.setMinutes(0, 0, 0);
  for (; d.getTime() / 1000 <= until; d.setHours(d.getHours() + 1)) {
    if (d.getTime() / 1000 < since || !onStep(d)) continue;
    const label = d.getHours() === 0
      ? d.toLocaleDateString([], { month: "short", day: "numeric" })
      : d.toLocaleTimeString([], { hour: "numeric" });
    ticks.push([d.getTime() / 1000, label]);
  }
  return ticks;
}

// A run of points, in pixels and in time order, as a smooth curve through every one that never bends past one, so
// a peak or dip drawn is one read: cubic Béziers with Steffen's monotone tangents, as d3's curveMonotoneX draws. Two
// points are a straight line, and one draws nothing, as a line through it would.
function smoothPath(points) {
  const at = ([px, py]) => `${px.toFixed(1)},${py.toFixed(1)}`;
  const n = points.length;
  if (n < 3) return points.map((p, i) => (i ? "L" : "M") + at(p)).join("");
  const h = points.slice(1).map((p, i) => p[0] - points[i][0]);
  const slope = h.map((dx, i) => (dx ? (points[i + 1][1] - points[i][1]) / dx : 0));
  const m = new Array(n).fill(0); // each point's tangent
  for (let i = 1; i < n - 1; i++) {
    const p = (slope[i - 1] * h[i] + slope[i] * h[i - 1]) / (h[i - 1] + h[i] || 1);
    m[i] = (Math.sign(slope[i - 1]) + Math.sign(slope[i])) * Math.min(Math.abs(slope[i - 1]), Math.abs(slope[i]), 0.5 * Math.abs(p)) || 0;
  }
  m[0] = (3 * slope[0] - m[1]) / 2;
  m[n - 1] = (3 * slope[n - 2] - m[n - 2]) / 2;
  let d = "M" + at(points[0]);
  for (let i = 0; i < n - 1; i++) {
    const [[x0, y0], [x1, y1]] = [points[i], points[i + 1]];
    const dx = (x1 - x0) / 3;
    d += `C${at([x0 + dx, y0 + m[i] * dx])} ${at([x1 - dx, y1 - m[i + 1] * dx])} ${at([x1, y1])}`;
  }
  return d;
}

function drawChart(chart) {
  const width = chart.plot.clientWidth;
  if (!width || !state.history) return;
  const { metric, svg: root } = chart;
  const height = CHART_HEIGHT;
  const m = { top: 12, right: 16, bottom: 28, left: 44 };
  const until = Date.now() / 1000;
  const since = until - state.hours * 3600;
  const gap = Math.max(3 * state.history.bucket, 180); // a longer silence breaks the line

  const series = state.sensors.map((sensor, i) => ({
    sensor,
    index: i,
    color: seriesColor(i),
    dash: seriesDash(i, 2),
    points: (state.history.series[sensor.id] || [])
      .filter((p) => p[0] >= since && p[metric.index] != null) // a sensor that reads only temperature has no humidity
      .map((p) => [p[0], metric.value(p[metric.index])]),
  })).filter((s) => shown(chart, s.sensor)); // the scale fits the rooms shown
  const values = series.flatMap((s) => s.points.map((p) => p[1]));
  root.setAttribute("viewBox", `0 0 ${width} ${height}`);
  root.replaceChildren();

  if (!values.length) {
    chart.layout = null;
    chart.tooltip.hidden = true;
    const empty = svg("text", { x: width / 2, y: height / 2, "text-anchor": "middle", class: "empty" });
    empty.textContent = series.length ? "No readings in this range yet" : "Every room is hidden";
    root.append(empty);
    return;
  }

  const [low, high, step] = niceScale(Math.min(...values), Math.max(...values), metric.minSpan);
  const x = (t) => m.left + ((t - since) / (until - since)) * (width - m.left - m.right);
  const y = (v) => m.top + ((high - v) / (high - low)) * (height - m.top - m.bottom);

  for (let k = 0; low + k * step <= high + step / 2; k++) {
    const v = low + k * step;
    const stroke = k === 0 ? "var(--axis)" : "var(--grid)"; // the bottom line is the baseline
    root.append(svg("line", { x1: m.left, x2: width - m.right, y1: y(v), y2: y(v), stroke, "stroke-width": 1 }));
    const label = svg("text", { x: m.left - 8, y: y(v), dy: "0.32em", "text-anchor": "end", class: "tick" });
    label.textContent = Number.isInteger(step) ? v.toFixed(0) : v.toFixed(1);
    root.append(label);
  }
  for (const [t, text] of timeTicks(since, until, width - m.left - m.right)) {
    const label = svg("text", { x: x(t), y: height - 8, "text-anchor": "middle", class: "tick" });
    label.textContent = text;
    root.append(label);
  }

  for (const s of series) {
    // Each run of points between gaps, as a smooth curve through them.
    const runs = [];
    s.points.forEach(([t, v], i) => {
      if (i === 0 || t - s.points[i - 1][0] > gap) runs.push([]);
      runs[runs.length - 1].push([x(t), y(v)]);
    });
    const d = runs.map(smoothPath).join("");
    root.append(svg("path", {
      d,
      fill: "none",
      stroke: s.color,
      "stroke-width": 2,
      "stroke-dasharray": s.dash,
      "stroke-linejoin": "round",
      "stroke-linecap": "round",
    }));
  }
  for (const s of series) {
    const last = s.points[s.points.length - 1];
    if (last) root.append(dot(x(last[0]), y(last[1]), s.color));
  }

  const crosshair = svg("line", { y1: m.top, y2: height - m.bottom, stroke: "var(--axis)", "stroke-width": 1 });
  const hoverDots = svg("g");
  crosshair.style.display = "none";
  root.append(crosshair, hoverDots);
  chart.layout = { since, until, x, y, m, width, gap, series, crosshair, hoverDots };
  if (chart.hoverTime != null) setHover(chart, snap(chart.layout, chart.hoverTime));
}

function dot(cx, cy, color) {
  // The surface-colored ring keeps a dot legible where it sits on other lines.
  return svg("circle", { cx, cy, r: 4, fill: color, stroke: "var(--surface)", "stroke-width": 2 });
}

// --- Hover and keyboard readout ------------------------------------------

function nearest(points, t) {
  let [lo, hi] = [0, points.length - 1];
  if (hi < 0) return null;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (points[mid][0] < t) lo = mid + 1;
    else hi = mid;
  }
  const before = points[lo - 1];
  return before && t - before[0] < points[lo][0] - t ? before : points[lo];
}

// The data time nearest to t, across every room.
function snap(layout, t) {
  let best = null;
  for (const s of layout.series) {
    const p = nearest(s.points, t);
    if (p && (best == null || Math.abs(p[0] - t) < Math.abs(best - t))) best = p[0];
  }
  return best;
}

function pointerHover(chart, event) {
  const layout = chart.layout;
  if (!layout) return;
  const px = event.clientX - chart.svg.getBoundingClientRect().left;
  const { m, width, since, until } = layout;
  if (px < m.left || px > width - m.right) return setHover(chart, null);
  setHover(chart, snap(layout, since + ((px - m.left) / (width - m.left - m.right)) * (until - since)));
}

function keyHover(chart, event) {
  const layout = chart.layout;
  if (!layout || chart.hoverTime == null) return;
  const moves = { ArrowLeft: -1, ArrowRight: 1 };
  if (event.key === "Escape") return setHover(chart, null);
  if (!(event.key in moves)) return;
  event.preventDefault();
  const step = state.history.bucket * moves[event.key];
  // Step one bucket, then snap; jump ahead if snapping lands back where we were.
  let t = snap(layout, chart.hoverTime + step);
  if (t === chart.hoverTime) t = snap(layout, chart.hoverTime + 2 * step);
  setHover(chart, t);
}

function setHover(chart, t) {
  chart.hoverTime = t;
  const { layout, tooltip } = chart;
  if (!layout) {
    tooltip.hidden = true;
    return;
  }
  const { crosshair, hoverDots } = layout;
  hoverDots.replaceChildren();
  if (t == null) {
    crosshair.style.display = "none";
    tooltip.hidden = true;
    return;
  }
  const cx = layout.x(t);
  crosshair.setAttribute("x1", cx);
  crosshair.setAttribute("x2", cx);
  crosshair.style.display = "";

  const rows = [];
  layout.series.forEach((s) => {
    const p = nearest(s.points, t);
    const value = p && Math.abs(p[0] - t) <= layout.gap / 2 ? p[1] : null;
    if (value != null) hoverDots.append(dot(layout.x(p[0]), layout.y(value), s.color));
    rows.push(el(
      "div",
      { class: "row" },
      lineKey(s.index),
      el("strong", {}, value == null ? "–" : `${value.toFixed(chart.metric.digits)}${chart.metric.unit}`),
      el("span", { class: "name" }, s.sensor.name),
    ));
  });
  const when = new Date(t * 1000).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" });
  tooltip.replaceChildren(el("div", { class: "time" }, when), ...rows);
  tooltip.hidden = false;
  const maxLeft = layout.width - tooltip.offsetWidth - 4;
  tooltip.style.left = `${cx + 16 <= maxLeft ? cx + 16 : Math.max(0, cx - tooltip.offsetWidth - 16)}px`;
}

// --- Table view: every charted value, without hovering --------------------

function renderTable(chart) {
  const { metric } = chart;
  if (!state.history) return;
  const bucket = state.history.bucket;
  const since = Date.now() / 1000 - state.hours * 3600;
  const sensors = state.sensors.filter((sensor) => shown(chart, sensor));
  const rows = new Map();
  for (const sensor of sensors) {
    for (const p of state.history.series[sensor.id] || []) {
      if (p[0] < since || p[metric.index] == null) continue;
      const key = Math.floor(p[0] / bucket);
      if (!rows.has(key)) rows.set(key, { t: p[0], values: {} });
      rows.get(key).values[sensor.id] = metric.value(p[metric.index]);
    }
  }
  const head = el("tr", {}, el("th", {}, "Time"), ...sensors.map((s) => el("th", {}, s.name)));
  const body = [...rows.entries()]
    .sort((a, b) => b[0] - a[0])
    .map(([, row]) => el(
      "tr",
      {},
      el("td", {}, new Date(row.t * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })),
      ...sensors.map((s) => el("td", {}, row.values[s.id] == null ? "–" : row.values[s.id].toFixed(metric.digits))),
    ));
  chart.tableWrap.replaceChildren(el("table", {}, el("thead", {}, head), el("tbody", {}, ...body)));
}

// --- House map: each room colored by its latest temperature ---------------

function createMap() {
  const views = el("div", { class: "segmented", role: "group", "aria-label": "Map view" });
  const plot = el("div", { class: "plot" });
  const tooltip = el("div", { class: "tooltip", hidden: "" });
  // Each with the keys that do the same on the map, a step at a time, in its tooltip (mapKey).
  const button = (label, text, turn, tilt, keys) => {
    const node = el("button", { type: "button", "aria-label": label, title: keys ? `${label} (${keys})` : label }, text);
    node.addEventListener("click", () => moveMap(turn, tilt));
    return node;
  };
  // A touch drags only sideways, since up and down it scrolls the page, so a phone tilts with the buttons.
  // The detailed view also pans, dragged with shift or the right button, or with two fingers, and zooms: in on a
  // part by double-clicking it, about the pointer with the wheel or by pinching, or with the buttons.
  const hint = el(
    "span",
    { class: "hint" },
    "Drag to turn",
    el("span", { class: "with-mouse" }, " and tilt"),
    el("span", { class: "model-only" }, ", ", el("span", { class: "with-wheel" }, "shift-drag to pan, scroll or "), "double-click to zoom"),
  );
  const zoom = (label, text, factor, key) => {
    const node = el("button", { type: "button", class: "model-only", "aria-label": label, title: `${label} (${key})` }, text);
    node.addEventListener("click", () => zoomModel(factor));
    return node;
  };
  const turns = el(
    "div",
    { class: "turns" },
    hint,
    button("Turn left", "⟲", -45, 0, "Shift+←"),
    button("Turn right", "⟳", 45, 0, "Shift+→"),
    button("Look from higher", "↑", 0, 30, "Shift+↑"),
    button("Look from lower", "↓", 0, -30, "Shift+↓"),
    zoom("Zoom out", "−", 1 / 1.5, "−"),
    zoom("Zoom in", "+", 1.5, "+"),
    button("Reset the view", "Reset", null, null),
  );
  const root = svg("svg", { tabindex: 0, role: "img" });
  // The detailed view draws on a canvas, with a note in its place if it can't.
  const canvas = el("canvas", { tabindex: 0, role: "img" });
  const note = el("p", { class: "note", hidden: "" });
  const outside = el("div", { class: "outside" });
  plot.append(root, canvas, note, outside, tooltip);
  const trees = el("input", { type: "checkbox" });
  trees.addEventListener("change", () => {
    state.modelTrees = trees.checked;
    try {
      localStorage.setItem("house-monitor-model-trees", String(trees.checked));
    } catch {}
    drawMap();
  });
  // The levels in a row, and under them whether the trees show, and in which season.
  const cuts = el(
    "div",
    { class: "cuts" },
    el(
      "div",
      { class: "levels" },
      el("span", { class: "hint" }, "Show"),
      el("div", { class: "segmented", role: "group", "aria-label": "Show the house whole, or cut away to a level" }),
    ),
    el(
      "div",
      { class: "tree-options" },
      el("label", { class: "trees" }, trees, "Trees"),
      el("div", { class: "segmented seasons", role: "group", "aria-label": "The trees' season" }),
    ),
  );
  const coverage = el("div", { class: "coverage", hidden: "" });
  const listWrap = el("div", { class: "table-wrap" });
  const full = el("button", { type: "button", class: "full" });
  full.addEventListener("click", () => setMapFull(!state.mapFull));
  const list = el("details", { class: "table-view" }, el("summary", {}, "Everything in the detailed view, known or guessed"), listWrap);
  const figure = el(
    "figure",
    { class: "chart map" },
    el("figcaption", {}, el("h2", {}, "House map"), el("span", { class: "unit" }, "°F"), el("div", { class: "map-actions" }, views, full)),
    plot,
    el("div", { class: "map-foot" }, scaleLegend(), cuts, coverage, turns),
    list,
  );
  const map = { figure, svg: root, canvas, note, plot, tooltip, outside, views, full, cuts, coverage, turns, list, listWrap, layout: null, drag: null, pointers: new Map() };
  for (const surface of [root, canvas]) {
    surface.addEventListener("focus", () => setMapFocus(state.mapFocus ?? map.layout?.keys?.[0] ?? indoors()[0]?.id ?? null));
    surface.addEventListener("blur", () => setMapFocus(null));
    surface.addEventListener("keydown", mapKey);
    // A drag turns and tilts the 3D and detailed views. In the detailed view, one with the right or middle button,
    // or with shift, pans it instead, and two fingers pan it and pinch to zoom it.
    surface.addEventListener("pointerdown", (event) => {
      mapPointerHover(event); // a tap on a phone
      if (state.mapView === "plan") return;
      const { clientX: x, clientY: y, pointerType, pointerId } = event;
      map.pointers.set(pointerId, { x, y });
      try {
        surface.setPointerCapture(pointerId);
      } catch {} // a pointer already gone
      const model = state.mapView === "model";
      if (model && map.pointers.size === 2) {
        map.drag = { fingers: fingers(map.pointers), moved: true };
        return;
      }
      if (map.pointers.size > 1) return;
      const pan = model && (event.button === 1 || event.button === 2 || event.shiftKey);
      // A touch drags only sideways, as up and down scrolls the page, but for full screen, where there's none to scroll.
      map.drag = { x, y, last: { x, y }, angle: state.mapAngle, tilt: state.mapTilt, touch: pointerType === "touch" && !state.mapFull, pan, moved: false };
    });
    surface.addEventListener("pointermove", (event) => {
      const drag = map.drag;
      if (map.pointers.has(event.pointerId)) map.pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
      if (drag?.fingers) { // panning as far as their middle moves, zooming about it as far as they spread
        const now = fingers(map.pointers);
        if (!now) return;
        panModel(now.x - drag.fingers.x, now.y - drag.fingers.y);
        const box = canvas.getBoundingClientRect();
        if (drag.fingers.apart > 0) zoomModelAt(now.apart / drag.fingers.apart, now.x - box.left, now.y - box.top);
        drag.fingers = now;
        return;
      }
      const [dx, dy] = drag ? [event.clientX - drag.x, drag.touch ? 0 : event.clientY - drag.y] : [0, 0];
      if (drag && (drag.moved || Math.hypot(dx, dy) > 4)) {
        drag.moved = true;
        if (drag.pan) {
          panModel(event.clientX - drag.last.x, event.clientY - drag.last.y);
          drag.last = { x: event.clientX, y: event.clientY };
          return;
        }
        state.mapAngle = drag.angle - dx * 0.5;
        state.mapTilt = clampTilt(drag.tilt + dy * 0.5); // dragging down shows more of the top
        drawMapSoon();
      } else {
        mapPointerHover(event);
      }
    });
    // Lifting a finger of two ends their gesture; the one left does nothing until it's lifted too.
    const lift = (event) => {
      map.pointers.delete(event.pointerId);
      const moved = map.drag?.moved;
      if (map.drag?.fingers || !map.pointers.size) map.drag = null;
      if (moved && !map.pointers.size) renderMapTooltip(); // back, beside the room's new place
    };
    surface.addEventListener("pointerup", lift);
    surface.addEventListener("pointercancel", lift);
    surface.addEventListener("pointerleave", () => {
      if (!map.drag && document.activeElement !== surface) setMapFocus(null);
    });
  }
  canvas.addEventListener("contextmenu", (event) => event.preventDefault()); // the right button pans
  canvas.addEventListener("dblclick", (event) => {
    const box = canvas.getBoundingClientRect();
    const key = map.layout?.pick?.(event.clientX - box.left, event.clientY - box.top);
    zoomModel(key ? 1.75 : 0, key && map.layout.centerOf(key)); // on nothing, back out to the whole scene
  });
  // The wheel zooms about the pointer, as does a pinch on a trackpad (a wheel with ctrl), until the end of the
  // zoom, past which the wheel scrolls the page again, so the map doesn't trap it.
  canvas.addEventListener("wheel", (event) => {
    const unit = event.deltaMode === 1 ? 33 : event.deltaMode === 2 ? canvas.clientHeight : 1; // lines or pages, in px
    const step = event.ctrlKey ? 100 : 700; // a pinch's steps are fine; a wheel's notches, 100 px or so, each 1.15x
    const box = canvas.getBoundingClientRect();
    const zoomed = zoomModelAt(Math.exp((-event.deltaY * unit) / step), event.clientX - box.left, event.clientY - box.top);
    if (zoomed || event.ctrlKey) event.preventDefault(); // a pinch never zooms the page
  }, { passive: false });
  // A lost WebGL context is made again when the browser gives it back.
  canvas.addEventListener("webglcontextlost", (event) => {
    event.preventDefault();
    map.gl = null;
  });
  canvas.addEventListener("webglcontextrestored", () => {
    map.gl = undefined;
    drawMapSoon();
  });
  return map;
}

// The map filling the screen: the browser's own full screen where it has one for the page, else fixed over the
// page, which stops scrolling under it, as on a phone. Its views grow to fill what its caption and controls leave.
async function setMapFull(on) {
  const figure = state.map.figure;
  if (on && document.fullscreenEnabled && figure.requestFullscreen) {
    try {
      return await figure.requestFullscreen(); // fullscreenchange takes it from there
    } catch {} // refused, so over the page instead
  }
  if (!on && document.fullscreenElement) return document.exitFullscreen();
  showMapFull(on);
}

function showMapFull(on) {
  const map = state.map;
  state.mapFull = on;
  map.figure.classList.toggle("is-full", on);
  document.documentElement.classList.toggle("map-full", on && !document.fullscreenElement);
  const label = on ? "Exit full screen" : "Full screen";
  map.full.setAttribute("aria-label", label);
  map.full.setAttribute("title", label);
  map.full.setAttribute("aria-pressed", String(on));
  // Corners pointing out, or back in.
  const d = on ? "M7 3v4H3M13 3v4h4M17 13h-4v4M3 13h4v4" : "M3 7V3h4M13 3h4v4M17 13v4h-4M7 17H3v-4";
  const icon = svg("svg", { width: 20, height: 20, viewBox: "0 0 20 20", "aria-hidden": "true" });
  icon.append(svg("path", { d, fill: "none", stroke: "currentColor", "stroke-width": 1.75, "stroke-linecap": "round", "stroke-linejoin": "round" }));
  map.full.replaceChildren(icon);
  drawMapSoon();
}

// Two pointers' middle and how far apart they are, or null for fewer.
function fingers(pointers) {
  const [a, b] = [...pointers.values()];
  return b ? { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2, apart: Math.hypot(a.x - b.x, a.y - b.y) } : null;
}

function renderMapViews() {
  state.map.views.replaceChildren(...MAP_VIEWS.map(({ id, label }) => {
    const button = el("button", { type: "button", "aria-pressed": String(id === state.mapView) }, label);
    button.addEventListener("click", () => {
      state.mapView = id;
      try {
        localStorage.setItem("house-monitor-map-view", id);
      } catch {}
      renderMapViews();
      drawMap();
    });
    return button;
  }));
}

// The scale, coldest to warmest: the gradient the rooms are colored from
// (drawn by the stylesheet), with temperatures below it.
function scaleLegend() {
  const [low, high] = [TEMP_STOPS[0], TEMP_STOPS.at(-1)];
  const ticks = TEMP_TICKS.map((t) => el("span", { style: `left: ${((t - low) / (high - low)) * 100}%` }, String(t)));
  return el(
    "div",
    { class: "scale" },
    el("span", { class: "end" }, "Colder"),
    el("div", { class: "bar" }, el("div", { class: "gradient" }), el("div", { class: "ticks" }, ...ticks)),
    el("span", { class: "end" }, "Warmer"),
  );
}

// A reading's color on the scale, from its value as shown, to a tenth: a
// blend of the two nearest stops in OKLab, as the legend's gradient blends
// them. With it, the ink with more APCA contrast on it, for its label.
function tempColor(reading) {
  const stops = (state.tempStops ??= TEMP_STOPS.map((_, i) => {
    return oklab(getComputedStyle(document.documentElement).getPropertyValue(`--temp-${i}`).trim());
  }));
  const f = Math.round(fahrenheit(reading.temperature_c) * 10) / 10;
  const at = Math.min(Math.max((f - TEMP_STOPS[0]) / (TEMP_STOPS[1] - TEMP_STOPS[0]), 0), stops.length - 1);
  const i = Math.min(Math.floor(at), stops.length - 2);
  const rgb = srgb(stops[i].map((v, k) => v + (stops[i + 1][k] - v) * (at - i)));
  const [ink] = Object.entries(INKS).sort(([, a], [, b]) => Math.abs(apca(b, rgb)) - Math.abs(apca(a, rgb)))[0];
  return { fill: `#${rgb.map((c) => Math.round(c * 255).toString(16).padStart(2, "0")).join("")}`, ink };
}

function roomFill(sensor, empty) {
  return sensor.reading ? tempColor(sensor.reading).fill : empty;
}

// From "#rrggbb" to OKLab [L, a, b] (Björn Ottosson's matrices).
function oklab(hex) {
  const linear = (c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  const [r, g, b] = [1, 3, 5].map((i) => linear(parseInt(hex.slice(i, i + 2), 16) / 255));
  const [l, m, s] = [
    0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b,
    0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b,
    0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b,
  ].map(Math.cbrt);
  return [
    0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
  ];
}

// From OKLab back to sRGB, each channel from 0 to 1.
function srgb([L, a, b]) {
  const [l, m, s] = [
    L + 0.3963377774 * a + 0.2158037573 * b,
    L - 0.1055613458 * a - 0.0638541728 * b,
    L - 0.0894841775 * a - 1.291485548 * b,
  ].map((v) => v ** 3);
  const gamma = (c) => (c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055);
  return [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ].map((c) => Math.min(1, Math.max(0, gamma(c))));
}

// APCA 0.0.98G lightness contrast (Lc) of text on a background, both sRGB
// from 0 to 1. Negative means light text on a dark background.
function apca(text, background) {
  const y = ([r, g, b]) => {
    const v = 0.2126729 * r ** 2.4 + 0.7151522 * g ** 2.4 + 0.072175 * b ** 2.4;
    return v >= 0.022 ? v : v + (0.022 - v) ** 1.414;
  };
  const [t, bg] = [y(text), y(background)];
  if (Math.abs(bg - t) < 0.0005) return 0;
  const c = 1.14 * (bg > t ? bg ** 0.56 - t ** 0.57 : bg ** 0.65 - t ** 0.62);
  return Math.abs(c) < 0.1 ? 0 : (c - Math.sign(c) * 0.027) * 100;
}

function isStale(reading) {
  return Date.now() / 1000 - reading.ts > STALE_AFTER;
}

// The sensors in the house, as opposed to outside.
function indoors() {
  return state.sensors.filter((s) => !s.outside);
}

// Each room's boxes in the house, in feet, one per piece of its plan, then
// each area's (with no sensor). Unless every room has a plan, each floor's
// rooms go in a row instead, with the floors stacked in house order, and areas
// are left out.
function houseBoxes() {
  const box = (place, sensor) => (plan) => ({ place, sensor, name: place.name, floor: place.floor, ...plan });
  const rooms = indoors();
  if (rooms.every((s) => s.plan)) {
    return [
      ...rooms.flatMap((s) => [s.plan].flat().map(box(s, s))),
      ...state.areas.flatMap((a) => [a.plan].flat().map(box(a, null))),
    ];
  }
  const floors = [...new Set(rooms.map((s) => s.floor))];
  const placed = {};
  return rooms.map((sensor) => {
    const k = (placed[sensor.floor] = (placed[sensor.floor] ?? -1) + 1);
    const elevation = (floors.length - 1 - floors.indexOf(sensor.floor)) * (ROOM_HEIGHT + 1);
    return box(sensor, sensor)({ x: k * 14, y: 0, width: 14, depth: 14, elevation, height: ROOM_HEIGHT });
  });
}

// The outline of boxes from above, grown by d, as loops of [x, y] corners:
// the edges between covered and uncovered cells of the grid their sides
// make, each loop going clockwise on screen.
function unionOutline(boxes, d = 0) {
  const rects = boxes.map((r) => [r.x - d, r.y - d, r.x + r.width + d, r.y + r.depth + d]);
  const xs = [...new Set(rects.flatMap((r) => [r[0], r[2]]))].sort((a, b) => a - b);
  const ys = [...new Set(rects.flatMap((r) => [r[1], r[3]]))].sort((a, b) => a - b);
  const covered = (i, j) => {
    const [x, y] = [(xs[i] + xs[i + 1]) / 2, (ys[j] + ys[j + 1]) / 2]; // NaN, so uncovered, off the grid
    return rects.some(([x0, y0, x1, y1]) => x0 < x && x < x1 && y0 < y && y < y1);
  };
  const edges = new Map(); // from each grid corner, the outline edges leaving it
  const edge = (from, to) => edges.set(from.join(), [...(edges.get(from.join()) ?? []), to]);
  for (let i = 0; i < xs.length - 1; i++) {
    for (let j = 0; j < ys.length - 1; j++) {
      if (!covered(i, j)) continue;
      if (!covered(i, j - 1)) edge([i, j], [i + 1, j]);
      if (!covered(i + 1, j)) edge([i + 1, j], [i + 1, j + 1]);
      if (!covered(i, j + 1)) edge([i + 1, j + 1], [i, j + 1]);
      if (!covered(i - 1, j)) edge([i, j + 1], [i, j]);
    }
  }
  const loops = [];
  for (const start of edges.keys()) {
    const loop = [];
    for (let at = start; edges.get(at).length; ) {
      const [i, j] = at.split(",").map(Number);
      loop.push([xs[i], ys[j]]);
      at = edges.get(at).pop().join();
    }
    if (loop.length) loops.push(loop);
  }
  return loops;
}

let mapPending = false;

function drawMapSoon() {
  if (mapPending) return;
  mapPending = true;
  requestAnimationFrame(() => {
    mapPending = false;
    drawMap();
  });
}

function drawMap() {
  const map = state.map;
  const width = map.plot.clientWidth;
  if (!width || !state.sensors.length) return;
  // Full screen, each view fills the height its plot has; else it takes the height it needs at this width.
  const fill = state.mapFull && map.plot.clientHeight > 120 ? map.plot.clientHeight : null;
  const view = state.mapView;
  const model = view === "model";
  map.figure.classList.toggle("is-model", model);
  map.turns.hidden = view === "plan";
  map.cuts.hidden = map.list.hidden = map.coverage.hidden = !model;
  map.svg.style.display = model ? "none" : "";
  if (model) {
    map.layout = drawModel(width, fill);
    const by = state.modelCoverageBy === "water" ? "how much water the sprinkler heads put on them in an hour of each zone" : "how many sprinkler heads reach them";
    const coverage = modelCoverageShown(state.modelScene) ? ` The lawn and beds are shaded by ${by}, as the key below says.` : "";
    map.canvas.setAttribute("aria-label", "The house in 3D with its doors, windows, vents, appliances, closets, roof and yard. "
      + `Arrow keys step through its parts, and with shift turn and tilt it; plus and minus zoom it; the list below the map names each one.${coverage}`);
  } else {
    map.canvas.style.display = "none";
    map.note.hidden = true;
    map.svg.replaceChildren();
    map.svg.setAttribute("aria-label", `House map${view === "3d" ? " in 3D" : ""}, each room colored by its temperature. `
      + `Arrow keys step through the rooms${view === "3d" ? ", and with shift turn and tilt it" : ""}; the room cards above list every reading.`);
    map.layout = view === "3d" ? draw3d(map.svg, width, fill) : drawPlan(map.svg, width, fill);
    map.svg.setAttribute("viewBox", `0 0 ${width} ${map.layout.height}`);
    map.svg.setAttribute("height", map.layout.height);
  }
  renderOutside();
  drawMapFocus();
}

// Readings from outside, which have no room to color: in the map's top-right
// corner, in every view, with the swatch of their temperature on the scale
// but in the detailed view, which has no temperature colors. The floor plan
// and 3D view's background takes that color too, faded into the surface, so
// the rooms, at full strength, and the labels on it stand out.
function renderOutside() {
  const weather = state.mapView !== "model" && state.sensors.find((s) => s.outside && s.reading);
  state.map.svg.style.background = weather
    ? `color-mix(in oklab, ${tempColor(weather.reading).fill} 30%, var(--surface))`
    : "";
  state.map.outside.replaceChildren(...state.sensors.filter((s) => s.outside).map((sensor) => {
    const reading = sensor.reading;
    return el(
      "div",
      {},
      el("span", { class: "swatch", style: `background: ${roomFill(sensor, "transparent")}`, "aria-hidden": "true" }),
      sensor.name,
      el("strong", {}, reading ? `${fahrenheit(reading.temperature_c).toFixed(1)}°F` : "–"),
      reading?.humidity != null ? `${Math.round(reading.humidity)}%` : "",
      reading && isStale(reading) ? el("span", { class: "chip-stale", title: "No recent reading" }, "⚠") : "",
    );
  }));
}

// The focused room's outline and readout. Drawn apart from the rooms, so
// focusing one (a tap included) leaves the elements under the pointer alone.
function drawMapFocus() {
  const { focus, outlines, highlight } = state.map.layout;
  if (highlight) {
    highlight(state.mapFocus); // the detailed view, which draws the focused part lit up
    renderMapTooltip();
    return;
  }
  focus.replaceChildren(...(outlines[state.mapFocus] ?? []).map((points) => svg("polygon", {
    points,
    fill: "none",
    stroke: "var(--text-primary)",
    "stroke-width": 2,
    "stroke-linejoin": "round",
  })));
  renderMapTooltip();
}

// One plan per floor, side by side when they fit, all at the same scale,
// inset from the edges of the map's background.
function drawPlan(root, width, fill = null) {
  const boxes = houseBoxes();
  const house = extent(boxes);
  const floors = [...new Set(boxes.map((r) => r.floor))];
  const [gap, head, pad] = [24, 24, 12];
  // The floors in columns, as many as fit at 240 px or more each, each no taller than 300 px; or filling a height,
  // in however many columns show them largest, each centered in its place.
  const layout = (columns) => {
    const rows = Math.ceil(floors.length / columns);
    const cell = (width - 2 * pad - gap * (columns - 1)) / columns;
    const tall = fill ? (fill - 2 * pad - rows * head - (rows - 1) * gap) / rows : 300;
    return { columns, rows, cell, scale: Math.max(0, Math.min(cell / house.width, tall / house.depth)) };
  };
  const { columns, rows, cell, scale } = fill
    ? floors.map((_, i) => layout(i + 1)).reduce((best, next) => (next.scale > best.scale ? next : best))
    : layout(Math.max(1, Math.min(floors.length, Math.floor((width - 2 * pad + gap) / (240 + gap)))));
  const needed = 2 * pad + rows * (head + house.depth * scale) + (rows - 1) * gap;
  const height = fill ?? needed;
  const [inset, drop] = fill ? [(cell - house.width * scale) / 2, (fill - needed) / 2] : [0, 0];
  const [anchors, outlines] = [{}, {}];

  floors.forEach((floor, f) => {
    const left = pad + (f % columns) * (cell + gap) + inset;
    const top = pad + Math.floor(f / columns) * (head + house.depth * scale + gap) + head + drop;
    const place = (r) => ({
      x: left + (r.x - house.x) * scale,
      y: top + (r.y - house.y) * scale,
      width: r.width * scale,
      height: r.depth * scale,
    });
    const title = svg("text", { x: left, y: top - 8, class: "map-floor" });
    title.textContent = `${floor} level`;
    // The whole house's outline, so each plan shows where its floor sits:
    // every room's edge, filled over so only the outside edge shows.
    const outline = svg("g", { "aria-hidden": "true" });
    const grow = (box, d) => ({ x: box.x - d, y: box.y - d, width: box.width + 2 * d, height: box.height + 2 * d });
    for (const r of boxes) outline.append(svg("rect", { ...grow(place(r), 0.5), fill: "none", stroke: "var(--axis)", "stroke-width": 2 }));
    for (const r of boxes) outline.append(svg("rect", { ...grow(place(r), 0.5), fill: "var(--surface)" }));
    root.append(title, outline);

    // The areas, then the rooms. A room's pieces are filled a little past
    // their edges, so no seam shows between them, and each room or area is
    // walled as one shape and labeled in its largest piece.
    const corner = ([x, y]) => `${left + (x - house.x) * scale},${top + (y - house.y) * scale}`;
    const shape = (pieces, d = 0) => unionOutline(pieces, d / scale).map((loop) => loop.map(corner).join(" "));
    const onFloor = boxes.filter((r) => r.floor === floor);
    for (const p of new Set([...onFloor.filter((r) => !r.sensor), ...onFloor.filter((r) => r.sensor)].map((r) => r.place))) {
      const pieces = onFloor.filter((r) => r.place === p);
      const sensor = pieces[0].sensor;
      const box = place(pieces.reduce((a, b) => (b.width * b.depth > a.width * a.depth ? b : a)));
      const group = svg("g", sensor ? { "data-sensor": sensor.id } : {});
      if (sensor) group.append(...pieces.map((r) => svg("rect", { ...grow(place(r), 0.5), fill: roomFill(sensor, "var(--surface)") })));
      group.append(...shape(pieces).map((points) => svg("polygon", { points, fill: "none", stroke: "var(--wall)", "stroke-width": 1 })));
      root.append(group);
      if (!sensor) {
        areaLabel(root, p.name, [box.x + box.width / 2, box.y + box.height / 2], box.width - 8);
        continue;
      }
      roomLabel(group, sensor, box);
      anchors[sensor.id] = box;
      outlines[sensor.id] = shape(pieces, 2);
    }
  });
  const focus = svg("g", { "pointer-events": "none" });
  root.append(focus);
  return { height, anchors, outlines, focus };
}

// The room's name, temperature and humidity, as many as fit inside it.
function roomLabel(group, sensor, box) {
  const reading = sensor.reading;
  const room = box.width - 8;
  const name = sensor.name + (reading && isStale(reading) ? " ⚠" : "");
  const lines = [
    ...nameLines(group, name, room).map((text) => ({ kind: "name", text, size: 12, weight: 600 })),
    { kind: "value", text: reading ? `${fahrenheit(reading.temperature_c).toFixed(1)}°` : "–", size: 20, weight: 600 },
    { kind: "humidity", text: reading?.humidity != null ? `${Math.round(reading.humidity)}%` : "", size: 12, weight: 400 },
  ];
  const lead = (line) => Math.round(line.size * 1.3);
  const total = (shown) => shown.reduce((sum, line) => sum + lead(line), 0);
  let shown = lines.filter((line) => line.text);
  const padding = (shown) => (shown.length > 1 ? 12 : 4); // a line alone can sit closer to the walls
  for (const kind of ["humidity", "name", "value"]) {
    if (total(shown) + padding(shown) <= box.height) break;
    shown = shown.filter((line) => line.kind !== kind); // humidity goes first, then the name
  }
  const ink = reading ? tempColor(reading).ink : "var(--text-secondary)";
  let y = box.y + (box.height - total(shown)) / 2;
  for (const line of shown) {
    const text = svg("text", {
      x: box.x + box.width / 2,
      y: y + lead(line) / 2,
      dy: "0.35em",
      "text-anchor": "middle",
      fill: ink,
      "font-size": line.size,
      "font-weight": line.weight,
    });
    text.textContent = line.text;
    group.append(text);
    // A name shrinks a size or two to fit a narrow room; past that, the readout still has it.
    for (let size = line.size; text.getComputedTextLength() > room; size--) {
      if (line.kind === "value" || size <= 10) {
        text.remove();
        break;
      }
      text.setAttribute("font-size", size - 1);
    }
    y += lead(line);
  }
}

// A room's name on one line if it fits, shrunk as far as 10px. Or else on
// two lines, broken between words where the wider half is narrowest, if both
// halves fit at full size.
function nameLines(group, name, room) {
  const width = (text, size = 12) => {
    const node = svg("text", { "font-size": size, "font-weight": 600 });
    node.textContent = text;
    group.append(node);
    const length = node.getComputedTextLength();
    node.remove();
    return length;
  };
  if (width(name, 10) <= room) return [name];
  const words = name.split(" ");
  const [best] = words.slice(1)
    .map((_, k) => [words.slice(0, k + 1).join(" "), words.slice(k + 1).join(" ")])
    .map((halves) => [halves, Math.max(...halves.map(width))])
    .sort((a, b) => a[1] - b[1]);
  return best && best[1] <= room ? best[0] : [name];
}

// The house seen from any direction, each room a box: turned by
// state.mapAngle, and tilted by state.mapTilt from straight below to straight above.
function draw3d(root, width, fill = null) {
  const boxes = houseBoxes();
  const house = extent(boxes);
  const [cx, cy, cz] = [house.x + house.width / 2, house.y + house.depth / 2, (house.low + house.high) / 2];
  const [turn, tilt] = [state.mapAngle, state.mapTilt].map((degrees) => (degrees * Math.PI) / 180);
  const toward = [Math.sin(turn), Math.cos(turn)]; // the viewer's direction across the plan
  const [cosTilt, sinTilt] = [Math.cos(tilt), Math.sin(tilt)];
  const view = [toward[0] * cosTilt, toward[1] * cosTilt, sinTilt]; // from the house toward the viewer
  // Sized to the house's bounding sphere, so turning or tilting it neither
  // zooms it nor changes the map's height and moves the page; full screen, as
  // large as fits the height to fill.
  const radius = Math.hypot(house.width, house.depth, house.high - house.low) / 2;
  const pad = 22;
  const scale = Math.min(width - 2 * pad, fill ? fill - 2 * pad : 480) / (2 * radius);
  const height = fill ?? Math.ceil(2 * radius * scale + 2 * pad);
  const [ox, oy] = [width / 2, height / 2];
  const project = (x, y, z) => {
    const [dx, dy, dz] = [x - cx, y - cy, z - cz];
    const across = dx * toward[1] - dy * toward[0];
    const near = dx * toward[0] + dy * toward[1];
    return [ox + across * scale, oy + (near * sinTilt - dz * cosTilt) * scale];
  };

  const order = paintOrder(boxes, view);
  const faces = new Map(order.map((r) => [r, boxFaces(r, project, view)]));
  const pointList = (face) => face.points.map((p) => p.map((v) => v.toFixed(1)).join(",")).join(" ");
  for (const r of order) {
    const group = svg("g", r.sensor ? { "data-sensor": r.sensor.id } : {});
    const fill = r.sensor && roomFill(r.sensor, null);
    for (const face of faces.get(r)) {
      const polygon = svg("polygon", { points: pointList(face), "stroke-linejoin": "round" });
      if (fill) {
        // Walls are shaded darker the more they turn away to the right.
        polygon.style.fill = face.shade ? `color-mix(in oklab, ${fill} ${100 - face.shade}%, black)` : fill;
        polygon.setAttribute("stroke", "var(--surface)");
        polygon.setAttribute("stroke-width", 1.5);
      } else {
        // No sensor, or no reading yet: an empty frame, not a color on the scale.
        polygon.setAttribute("fill", "none");
        polygon.setAttribute("stroke", "var(--wall)");
        polygon.setAttribute("pointer-events", "visible");
      }
      group.append(polygon);
    }
    root.append(group);
  }
  // The focus outline goes over every room, so a hidden room shows through, but under the labels.
  const focus = svg("g", { "pointer-events": "none" });
  root.append(focus);
  const outlines = {};
  for (const r of order.filter((r) => r.sensor)) (outlines[r.sensor.id] ??= []).push(...faces.get(r).map(pointList));

  // Each label goes on the first part of the room nothing in front hides: the
  // middle of a top, then of the wall facing the viewer most, then nearer
  // their edges. A room hidden from this angle gets no label.
  const candidates = new Map(); // for each room or area: [its first box, [{facing, at}], its top's middle]
  order.forEach((r, k) => {
    const hiding = order.slice(k + 1).flatMap((other) => faces.get(other).map((face) => face.points));
    const entry = candidates.get(r.place) ?? [r, [], project(...faces.get(r).at(-1).center)];
    entry[1].push(...faces.get(r)
      .flatMap((face) => LABEL_SPOTS.map(([u, v]) => ({ facing: face.facing, at: along(face.points, u, v) })))
      .filter(({ at }) => !hiding.some((polygon) => inside(at, polygon))));
    candidates.set(r.place, entry);
  });
  const spots = [...candidates.values()].map(([r, visible, top]) => [r, visible.sort((a, b) => b.facing - a.facing).map(({ at }) => at), top]);
  const rooms = order.filter((r) => r.sensor).flatMap((r) => faces.get(r).map((face) => face.points));
  const anchors = placeChips(root, spots, rooms, false) ?? placeChips(root, spots.reverse(), rooms, true);
  return { height, anchors, outlines, focus };
}

// Full labels if none collide. Otherwise just temperatures, front rooms first,
// leaving out any that still collide: the readout and room cards have them.
// Area names go last, only beside full labels, at the first of their visible
// spots clear of the rooms and the other labels.
function placeChips(root, spots, rooms, compact) {
  const layer = svg("g");
  root.append(layer);
  const [anchors, placed] = [{}, []];
  const apart = (a, b) => a.x + a.width + 4 <= b.x || b.x + b.width + 4 <= a.x
    || a.y + a.height + 4 <= b.y || b.y + b.height + 4 <= a.y;
  for (const [{ sensor }, [spot], top] of spots.filter(([r]) => r.sensor)) {
    const point = (p) => ({ x: p[0] - 1, y: p[1] - 1, width: 2, height: 2 });
    if (!spot) {
      anchors[sensor.id] = point(top); // the readout still points at it
      continue;
    }
    const [chip, box] = roomChip(layer, sensor, spot, compact);
    if (placed.every((other) => apart(box, other))) {
      placed.push(box);
      anchors[sensor.id] = box;
    } else if (compact) {
      chip.remove();
      anchors[sensor.id] = point(spot);
    } else {
      layer.remove();
      return null;
    }
  }
  const clear = (box) => [0, 0.5, 1].every((u) => [0, 0.5, 1].every((v) => {
    return !rooms.some((polygon) => inside([box.x + u * box.width, box.y + v * box.height], polygon));
  }));
  for (const [area, visible] of compact ? [] : spots.filter(([r]) => !r.sensor)) {
    if (!visible.length) continue;
    const [label, box] = areaLabel(layer, area.name, [0, 0]);
    const at = ([x, y]) => ({ ...box, x: box.x + x, y: box.y + y });
    const spot = visible.find((p) => clear(at(p)) && placed.every((other) => apart(at(p), other)));
    if (!spot) {
      label.remove();
      continue;
    }
    label.setAttribute("x", spot[0]);
    label.setAttribute("y", spot[1]);
    placed.push(at(spot));
  }
  return anchors;
}

// The name of a part of the house with no sensor, in muted text; none if it won't fit.
function areaLabel(root, name, [x, y], room = Infinity) {
  const text = svg("text", { x, y, dy: "0.35em", "text-anchor": "middle", class: "area-name" });
  text.textContent = name;
  root.append(text);
  const width = text.getComputedTextLength();
  if (width > room) {
    text.remove();
    return [null, null];
  }
  return [text, { x: x - width / 2 - 2, y: y - 8, width: width + 4, height: 16 }];
}

// Back to front. Each axis that separates two boxes says which is nearer: the
// one on the viewer's side along it, so the higher one from above and the
// lower from below. If two axes disagree, the boxes can't overlap on screen,
// so their order doesn't matter, and ordering them anyway could make a cycle.
function paintOrder(boxes, view) {
  const behind = (a, b) => {
    const votes = [];
    const axis = (lo1, hi1, lo2, hi2, viewer) => {
      if (viewer && hi1 <= lo2) votes.push(viewer > 0);
      else if (viewer && hi2 <= lo1) votes.push(viewer < 0);
    };
    axis(a.x, a.x + a.width, b.x, b.x + b.width, view[0]);
    axis(a.y, a.y + a.depth, b.y, b.y + b.depth, view[1]);
    axis(a.elevation, a.elevation + a.height, b.elevation, b.elevation + b.height, view[2]);
    return votes.length > 0 && votes.every((v) => v);
  };
  const left = [...boxes];
  const order = [];
  while (left.length) {
    const k = left.findIndex((a) => left.every((b) => b === a || !behind(b, a)));
    order.push(...left.splice(Math.max(k, 0), 1)); // k is -1 only for a cycle, which simple plans don't make
  }
  return order;
}

// The faces of a room's box the viewer can see: the walls facing them, and
// its top from above or its floor from below. A ceiling with a drop slopes
// down to that wall, from its full height at the wall across from it.
function boxFaces(r, project, view) {
  const [x0, x1, y0, y1] = [r.x, r.x + r.width, r.y, r.y + r.depth];
  const z0 = r.elevation;
  const z1 = ceilingOf(r);
  const [fallX, fallY] = [z1(x0, y0) - z1(x0 + 1, y0), z1(x0, y0) - z1(x0, y0 + 1)]; // per foot
  const walls = [
    { normal: [0, -1], ends: [[x1, y0], [x0, y0]] },
    { normal: [1, 0], ends: [[x1, y1], [x1, y0]] },
    { normal: [0, 1], ends: [[x0, y1], [x1, y1]] },
    { normal: [-1, 0], ends: [[x0, y0], [x0, y1]] },
  ];
  const faces = [];
  const across = Math.hypot(view[0], view[1]) || 1;
  for (const { normal: [nx, ny], ends: [[ax, ay], [bx, by]] } of walls) {
    const facing = nx * view[0] + ny * view[1];
    if (facing <= 1e-6) continue;
    const right = (nx * view[1] - ny * view[0]) / across; // -1 faces left, 1 faces right
    faces.push({
      points: [project(ax, ay, z0), project(bx, by, z0), project(bx, by, z1(bx, by)), project(ax, ay, z1(ax, ay))],
      center: [(ax + bx) / 2, (ay + by) / 2, (z0 + z1((ax + bx) / 2, (ay + by) / 2)) / 2],
      facing,
      shade: Math.round(8 + 10 * (1 + right)),
    });
  }
  // A top steep enough to face away from the viewer is hidden.
  if (view[2] + fallX * view[0] + fallY * view[1] > 1e-6) {
    faces.push({
      points: [project(x0, y0, z1(x0, y0)), project(x1, y0, z1(x1, y0)), project(x1, y1, z1(x1, y1)), project(x0, y1, z1(x0, y1))],
      center: [(x0 + x1) / 2, (y0 + y1) / 2, z1((x0 + x1) / 2, (y0 + y1) / 2)],
      facing: 2, // a label's first choice
      shade: 0,
    });
  }
  if (view[2] < -1e-6) {
    faces.push({
      points: [project(x0, y0, z0), project(x0, y1, z0), project(x1, y1, z0), project(x1, y0, z0)],
      center: [(x0 + x1) / 2, (y0 + y1) / 2, z0],
      facing: 2,
      shade: 36, // the underside, darker than any wall
    });
  }
  return faces;
}

// A point on a projected four-cornered face, u along its first edge and v up its second.
function along([a, b, c, d], u, v) {
  return [0, 1].map((i) => (1 - v) * ((1 - u) * a[i] + u * b[i]) + v * ((1 - u) * d[i] + u * c[i]));
}

function inside([px, py], polygon) {
  let hit = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const [[xi, yi], [xj, yj]] = [polygon[i], polygon[j]];
    if (yi > py !== yj > py && px < ((xj - xi) * (py - yi)) / (yj - yi) + xi) hit = !hit;
  }
  return hit;
}

// A label on a surface-colored chip, so it reads whatever it sits on.
function roomChip(root, sensor, [x, y], compact) {
  const reading = sensor.reading;
  const group = svg("g", { "data-sensor": sensor.id });
  const rect = svg("rect", { rx: 6, fill: "var(--surface)", stroke: "var(--border)" });
  const value = svg("text", { x, y: compact ? y + 4.5 : y - 3, "text-anchor": "middle", class: "chip-value" });
  value.textContent = reading ? `${fahrenheit(reading.temperature_c).toFixed(1)}°` : "–";
  if (reading && isStale(reading)) {
    const icon = svg("tspan", { class: "chip-stale" });
    icon.textContent = " ⚠";
    value.append(icon);
  }
  const name = svg("text", { x, y: y + 12, "text-anchor": "middle", class: "chip-name" });
  name.textContent = sensor.name;
  group.append(rect, value, ...(compact ? [] : [name]));
  root.append(group);
  const width = Math.max(value.getComputedTextLength(), compact ? 0 : name.getComputedTextLength()) + (compact ? 12 : 16);
  const height = compact ? 22 : 38;
  const box = { x: x - width / 2, y: y - height / 2, width, height };
  for (const [key, v] of Object.entries(box)) rect.setAttribute(key, v);
  return [group, box];
}

function clampTilt(degrees) {
  return Math.max(-90, Math.min(90, degrees));
}

// Turn and tilt the 3D view by these degrees, or back to the first view, zoomed out, without them.
function moveMap(turn, tilt) {
  state.mapAngle = turn == null ? MAP_HOME.angle : state.mapAngle + turn;
  state.mapTilt = tilt == null ? MAP_HOME.tilt : clampTilt(state.mapTilt + tilt);
  if (turn == null) [state.modelZoom, state.modelTarget] = [null, null];
  drawMap();
}

function mapPointerHover(event) {
  const { layout, canvas } = state.map;
  if (layout?.pick) {
    const box = canvas.getBoundingClientRect();
    return setMapFocus(layout.pick(event.clientX - box.left, event.clientY - box.top));
  }
  const room = event.target.closest?.("[data-sensor]");
  setMapFocus(room ? room.dataset.sensor : null);
}

// The arrow keys step through the rooms, or the detailed view's parts. With shift they turn the 3D and detailed
// views, as the buttons below them do, a step a press: left and right turn it, up and down tilt it. Plus and
// minus zoom the detailed view in and out, = as plus so it needs no shift, but with ctrl or cmd they're left to
// zoom the page.
function mapKey(event) {
  const moves = { ArrowLeft: -1, ArrowRight: 1 };
  const turns = { ArrowLeft: [-MAP_KEY_TURN, 0], ArrowRight: [MAP_KEY_TURN, 0], ArrowUp: [0, MAP_KEY_TURN], ArrowDown: [0, -MAP_KEY_TURN] };
  const zooms = { "+": MAP_KEY_ZOOM, "=": MAP_KEY_ZOOM, "-": 1 / MAP_KEY_ZOOM, _: 1 / MAP_KEY_ZOOM };
  if (event.key === "Escape") return setMapFocus(null);
  if (event.key in zooms && state.mapView === "model" && !event.ctrlKey && !event.metaKey && !event.altKey) {
    event.preventDefault();
    return zoomModel(zooms[event.key]);
  }
  if (event.shiftKey && event.key in turns && state.mapView !== "plan") {
    event.preventDefault();
    return moveMap(...turns[event.key]);
  }
  const ids = state.map.layout?.keys ?? indoors().map((s) => s.id); // outside has no place to focus
  if (!(event.key in moves) || !ids.length) return;
  event.preventDefault();
  const i = ids.indexOf(state.mapFocus);
  setMapFocus(ids[i < 0 ? 0 : (i + moves[event.key] + ids.length) % ids.length]);
}

function setMapFocus(id) {
  if (id === state.mapFocus) return;
  state.mapFocus = id;
  if (state.map.layout) drawMapFocus();
}

function renderMapTooltip() {
  const { tooltip, layout, drag, plot } = state.map;
  const sensor = state.sensors.find((s) => s.id === state.mapFocus);
  const anchor = layout?.anchorOf ? layout.anchorOf(state.mapFocus) : sensor && layout?.anchors[sensor.id];
  if (!anchor || drag?.moved) {
    tooltip.hidden = true;
    return;
  }
  tooltip.replaceChildren(...(sensor ? roomReadout(sensor) : modelReadout(layout.item(state.mapFocus))));
  tooltip.hidden = false;
  // Beside the room if there's room, else below it, else above it.
  const [w, h, gap] = [tooltip.offsetWidth, tooltip.offsetHeight, 8];
  let [left, top] = [anchor.x + anchor.width + gap, anchor.y];
  if (left + w > plot.clientWidth) left = anchor.x - w - gap;
  if (left < 0) {
    left = Math.min(Math.max(0, anchor.x + (anchor.width - w) / 2), plot.clientWidth - w);
    top = anchor.y + anchor.height + gap;
    if (top + h > layout.height) top = Math.max(0, anchor.y - h - gap);
  }
  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${Math.max(0, Math.min(top, layout.height - h))}px`;
}

// A room's readout: its name and level, latest reading and how old it is.
function roomReadout(sensor) {
  const reading = sensor.reading;
  const rows = [el("div", { class: "head" }, el("strong", {}, sensor.name), " ", el("span", {}, `${sensor.floor} level`))];
  if (reading) {
    const age = Date.now() / 1000 - reading.ts;
    rows.push(
      el("div", { class: "row" }, el("strong", {}, `${fahrenheit(reading.temperature_c).toFixed(1)}°F`), el("span", { class: "name" }, "Temperature")),
      ...(reading.humidity == null ? [] : [el("div", { class: "row" }, el("strong", {}, `${Math.round(reading.humidity)}%`), el("span", { class: "name" }, "Humidity"))]),
      el("div", { class: "time" }, `Updated ${ago(age)}`),
    );
    if (isStale(reading)) {
      rows.push(el("div", { class: "stale" }, el("span", { class: "icon", "aria-hidden": "true" }, "⚠"), `No reading for ${duration(age)}`));
    }
  } else {
    rows.push(el("div", { class: "time" }, "No readings yet"));
  }
  return rows;
}

// --- Data ----------------------------------------------------------------

let drawPending = false;

function drawAll() {
  if (drawPending) return;
  drawPending = true;
  requestAnimationFrame(() => {
    drawPending = false;
    state.charts.forEach(drawChart);
  });
}

async function loadSensors() {
  const response = await fetch("/api/sensors");
  ({ sensors: state.sensors, areas: state.areas = [], details: state.details = null, wall: state.wall = null } = await response.json());
  for (const sensor of state.sensors) {
    sensor.latest = sensor.reading;
    showReading(sensor);
  }
  state.modelScene = null; // rebuilt from these when next drawn
  renderRooms();
  state.charts.forEach(renderLegend);
  drawMap();
}

async function loadHistory() {
  const request = ++state.historyRequest;
  state.charts.forEach((c) => c.figure.classList.add("is-loading"));
  const response = await fetch(`/api/history?hours=${state.hours}${state.hideSimulated ? "&simulated=0" : ""}`);
  const history = await response.json();
  if (request !== state.historyRequest) return; // a newer range was picked meanwhile
  state.history = history;
  for (const chart of state.charts) {
    chart.figure.classList.remove("is-loading");
    drawChart(chart);
    if (chart.details.open) renderTable(chart);
  }
}

function onReading(reading) {
  const sensor = state.sensors.find((s) => s.id === reading.sensor_id);
  if (!sensor) return;
  const charted = state.charts.map((chart) => chart.rooms(sensor));
  sensor.latest = reading;
  showReading(sensor);
  // A room that starts or stops reading humidity joins or leaves the humidity chart, as one does that starts or
  // stops being read by the simulator while its readings are hidden.
  state.charts.forEach((chart, i) => chart.rooms(sensor) !== charted[i] && renderLegend(chart));
  updateRooms();
  if (!sensor.reading) return; // hidden
  sensor.card.classList.remove("updated");
  void sensor.card.offsetWidth; // restart the arrival animation
  sensor.card.classList.add("updated");
  drawMapSoon();
  if (state.history) {
    const points = (state.history.series[reading.sensor_id] ||= []);
    const last = points[points.length - 1];
    if (!last || reading.ts - last[0] >= state.history.bucket) {
      points.push([reading.ts, reading.temperature_c, reading.humidity]);
      drawAll();
    }
  }
}

function setConnection(status, label) {
  const node = document.getElementById("connection");
  node.className = `connection is-${status}`;
  node.querySelector(".label").textContent = label;
}

function connect() {
  let dropped = false;
  const stream = new EventSource("/api/stream");
  stream.onopen = () => {
    setConnection("live", "Live");
    if (dropped) loadSensors().then(loadHistory); // fill in what we missed
    dropped = false;
  };
  stream.onerror = () => {
    setConnection("reconnecting", "Reconnecting…");
    dropped = true;
    if (stream.readyState === EventSource.CLOSED) setTimeout(connect, 5000);
  };
  stream.onmessage = (event) => onReading(JSON.parse(event.data));
}

function renderRanges() {
  const group = document.getElementById("ranges");
  group.replaceChildren(...RANGES.map(({ label, hours }) => {
    const button = el("button", { type: "button", "aria-pressed": String(hours === state.hours) }, label);
    button.addEventListener("click", () => {
      state.hours = hours;
      try {
        localStorage.setItem("house-monitor-hours", String(hours));
      } catch {}
      renderRanges();
      loadHistory();
    });
    return button;
  }));
}

async function main() {
  try {
    const saved = Number(localStorage.getItem("house-monitor-hours"));
    if (RANGES.some((r) => r.hours === saved)) state.hours = saved;
    const view = localStorage.getItem("house-monitor-map-view");
    if (MAP_VIEWS.some((v) => v.id === view)) state.mapView = view;
    state.modelCut = localStorage.getItem("house-monitor-model-cut") ?? state.modelCut; // checked when drawn
    state.modelTrees = localStorage.getItem("house-monitor-model-trees") !== "false";
    state.modelCoverage = localStorage.getItem("house-monitor-model-coverage") === "true";
    state.modelCoverageBy = localStorage.getItem("house-monitor-model-coverage-by") === "water" ? "water" : "heads";
    state.hideSimulated = localStorage.getItem("house-monitor-hide-simulated") === "true";
  } catch {}
  renderSimulatedToggle();
  state.map = createMap();
  document.getElementById("map").append(state.map.figure);
  renderMapViews();
  renderRanges();
  state.charts = CHARTS.map(createChart);
  document.getElementById("charts").append(...state.charts.map((c) => c.figure));
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    state.tempStops = null; // the theme's colors
    state.modelScene = null;
    drawMap();
  });
  await loadSensors();
  await loadHistory();
  connect();
  setInterval(() => {
    updateRooms();
    drawMapSoon(); // the readout's age, and rooms going stale
  }, 1000);
  setInterval(drawAll, 60 * 1000); // slide the time axis even when no reading arrives
  new ResizeObserver(drawAll).observe(document.getElementById("charts"));
  new ResizeObserver(drawMapSoon).observe(document.getElementById("map"));
  showMapFull(false);
  document.addEventListener("fullscreenchange", () => showMapFull(document.fullscreenElement === state.map.figure));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && state.mapFull && !document.fullscreenElement) setMapFull(false);
  });
  window.addEventListener("resize", () => state.mapFull && drawMapSoon()); // as a phone turns, full screen
}

main();

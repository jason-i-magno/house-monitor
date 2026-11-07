"use strict";

// --- House map: the detailed view -------------------------------------------
//
// The house in 3D, drawn with WebGL: each room's walls, floor and closets, the
// doors, windows, vents and appliances in them, the roof over them and the
// yard around them, placed from the source's details (housemon/details.py).
// It's colored like the things themselves, not by temperature, and turns and
// tilts with the 3D view, and zooms in on any part of it. Cut away to a level,
// the roof, the trees and the levels above it come off, and walls stop
// MODEL_CUT ft above their floors, all but the outside walls at the back as
// seen, which stay whole.
//
// Each room's walls stand just inside the edges of its plan, so two rooms side
// by side each have their own, and never overlap. Each run of wall is split
// into cells around its doors and windows, and wherever what's beyond it
// changes, so each cell's outside face can take the color of what it faces:
// siding or foundation outside, paint against another room.

const MODEL_WALL = 0.33; // feet thick
const MODEL_CLOSET_WALL = 0.25;
const MODEL_CUT = 4; // feet above its floor where a cut-away wall stops
const MODEL_CLOSET_HEIGHT = 8; // a closet's walls at most, under a higher ceiling
const MODEL_CLOSET_DOOR = 6.67;
const MODEL_ROOF_DEPTH = 0.6; // feet from a roof's eaves down to its underside, as in details.Roof
const MODEL_SIZE = 720; // px at most across the scene's bounding sphere
const MODEL_PAD = 16;
const MODEL_ZOOM_MAX = 6; // times the house's framing
const MODEL_TOKENS = [
  "lawn", "soil", "concrete", "slab", "foundation", "siding", "trim", "roof", "glass", "door", "garage-door", "seam",
  "paint", "drywall", "cut", "carpet", "wood", "tile", "dirt", "white", "steel", "black", "grille", "trunk",
  "leaves", "needles", "focus", "sash", "paneling", "flagstone", "stone", "fence", "deck", "shed-roof", "brick", "gravel", "mulch",
];
const MODEL_KINDS = {
  door: "Door", window: "Window", "sliding door": "Sliding door", "garage door": "Garage door", opening: "Opening",
  vent: "Vent", appliance: "Appliance", plumbing: "Plumbing", fireplace: "Fireplace", "half wall": "Half wall", porch: "Porch", patio: "Patio", deck: "Deck", driveway: "Driveway", walk: "Walk", bed: "Bed",
  deciduous: "Tree", evergreen: "Evergreen tree",
};
const MODEL_GROUPS = ["Doors and windows", "Vents", "Appliances", "Plumbing", "Fireplaces", "Half walls", "Closets", "Steps", "Roof", "Yard"];
// Each side of a plan as a line: the axis it runs along, where it is across
// that axis, which way is inward (k), and where it starts and ends.
const SIDE_LINES = {
  back: (p) => ({ axis: "x", line: p.y, k: 1, a: p.x, b: p.x + p.width }),
  front: (p) => ({ axis: "x", line: p.y + p.depth, k: -1, a: p.x, b: p.x + p.width }),
  left: (p) => ({ axis: "y", line: p.x, k: 1, a: p.y, b: p.y + p.depth }),
  right: (p) => ({ axis: "y", line: p.x + p.width, k: -1, a: p.y, b: p.y + p.depth }),
};

// The point d feet inward of a side's line at s along it.
function linePoint({ axis, line, k }, s, d) {
  return axis === "x" ? [s, line + k * d] : [line + k * d, s];
}

function planOverlap(a, b) {
  return a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.depth && b.y < a.y + a.depth;
}

// What's left of [a, b] once the spans are taken out.
function subtract([a, b], spans) {
  let left = [[a, b]];
  for (const [c, d] of spans) {
    left = left.flatMap(([u, v]) => [[u, Math.min(v, c)], [Math.max(u, d), v]].filter(([p, q]) => q - p > 1e-6));
  }
  return left;
}

// The piece of a place other than `except` holding the point, between its base and ceiling, if any.
function occupiedAt(pieces, x, y, z, except) {
  return pieces.find(([q, p]) => {
    const inPlan = q !== except && p.x < x && x < p.x + p.width && p.y < y && y < p.y + p.depth;
    return inPlan && p.base - 1e-6 <= z && z <= p.top(x, y) + 1e-6;
  });
}

// For a source with no details: the rooms on a lawn, nothing below the ground.
function modelFallback(pieces) {
  const h = extent(pieces);
  const [x0, y0, x1, y1] = [h.x - 10, h.y - 10, h.x + h.width + 10, h.y + h.depth + 10];
  return { lot: [[x0, y0], [x1, y0], [x1, y1], [x0, y1]], grade: h.low, finishes: {} };
}

// Whether a point on the plan is inside a polygon, its corners in order.
function inPolygon([x, y], points) {
  let hit = false;
  points.forEach(([x1, y1], i) => {
    const [x2, y2] = points[(i + 1) % points.length];
    if (y1 > y !== y2 > y && x < ((x2 - x1) * (y - y1)) / (y2 - y1) + x1) hit = !hit;
  });
  return hit;
}

// Whether a roof section covers x, y on the plan, within its rectangle and any outline.
function onRoof(r, x, y) {
  return r.x < x && x < r.x + r.width && r.y < y && y < r.y + r.depth && (!r.outline || inPolygon([x, y], r.outline));
}

// The height of a roof section's top at x, y on the plan.
function roofTop(r, x, y) {
  if (r.ridge) {
    const [u, u0, u1] = r.ridge === "x" ? [y, r.y, r.y + r.depth] : [x, r.x, r.x + r.width];
    return r.eave + (r.peak - r.eave) * (1 - Math.abs(u - (u0 + u1) / 2) / ((u1 - u0) / 2));
  }
  const down = { left: (r.x + r.width - x) / r.width, right: (x - r.x) / r.width, back: (r.y + r.depth - y) / r.depth, front: (y - r.y) / r.depth }[r.drop];
  return r.peak - (r.peak - r.eave) * down;
}

// --- The scene: what's in the house and where, built once per set of details

function modelScene() {
  const rooms = indoors();
  if (!rooms.length || !rooms.every((s) => s.plan)) return { note: "The detailed view needs every room's plan." };
  const raw = [...rooms, ...state.areas].flatMap((q) => [q.plan].flat());
  const details = {
    finishes: {}, colors: {}, claddings: [], roofs: [], roof_vents: [], openings: [], closets: [], fixtures: [], grounds: [], trees: [], sheds: [], fences: [], steps: [],
    ...(state.details ?? modelFallback(raw)),
  };
  const items = [];
  const add = (item) => {
    item.id = items.length + 1; // 0 is nothing, in the picking buffer
    if (details.example) item.known = true; // a made-up house has nothing to check
    items.push(item);
    return item;
  };

  const places = [
    ...rooms.map((s) => ({ key: s.id, name: s.name, floor: s.floor, plan: s.plan, sensor: s })),
    ...state.areas.map((a, k) => ({ key: `area:${k}`, name: a.name, floor: a.floor, plan: a.plan })),
  ].map((q) => ({
    ...q,
    pieces: [q.plan].flat().map((p) => ({ ...p, top: ceilingOf(p) })),
    finish: { floor: "wood", walls: "paint", ...details.finishes[q.name] },
  }));
  const pieces = places.flatMap((q) => q.pieces.map((p) => [q, p]));
  for (const [q, p] of pieces) {
    // Its walls go down to the top of whatever's under it, or to its foundation's footing.
    const under = pieces.filter(([o, b]) => o !== q && planOverlap(b, p) && b.elevation + b.height <= p.elevation + 1e-6);
    p.ground = !under.length;
    p.base = p.ground ? Math.min(p.elevation - 1, details.grade - 0.5) : Math.max(...under.map(([, b]) => b.elevation + b.height));
    // Its sides but where another of its place's pieces adjoins it.
    p.walls = Object.fromEntries(Object.entries(SIDE_LINES).map(([side, sideLine]) => {
      const s = sideLine(p);
      const [x, y] = linePoint(s, s.a, -0.005); // a strip just outside the side
      const strip = s.axis === "x" ? { x, y, width: s.b - s.a, depth: 0 } : { x, y, width: 0, depth: s.b - s.a };
      const beside = q.pieces.filter((o) => o !== p && planOverlap(o, { ...strip, width: strip.width || 0.001, depth: strip.depth || 0.001 }));
      return [side, subtract([s.a, s.b], beside.map((o) => (s.axis === "x" ? [o.x, o.x + o.width] : [o.y, o.y + o.depth])))];
    }));
  }
  for (const q of places) {
    const box = extent(q.pieces);
    q.item = add({ key: q.key, name: q.name, kind: q.sensor ? "Room" : "Area", place: q, sensor: q.sensor, level: q.pieces[0].elevation, bbox: [box.x, box.y, box.low, box.x + box.width, box.y + box.depth, box.high] });
  }
  const named = (name) => places.find((q) => q.name === name);

  const openings = details.openings.flatMap((o, i) => {
    const q = named(o.room);
    if (!q) return [];
    const out = { left: (p) => p.x, right: (p) => -(p.x + p.width), back: (p) => p.y, front: (p) => -(p.y + p.depth) }[o.wall];
    const p = q.pieces.reduce((best, piece) => (out(piece) < out(best) ? piece : best));
    const side = SIDE_LINES[o.wall](p);
    const [s0, z0] = [side.a + o.offset, p.elevation + o.sill];
    const opening = { ...o, ...side, place: q, piece: p, s0, s1: s0 + o.width, z0, z1: z0 + o.height, through: o.kind !== "vent" };
    const [x, y] = linePoint(side, (opening.s0 + opening.s1) / 2, -0.01);
    opening.exterior = !occupiedAt(pieces, x, y, (z0 + opening.z1) / 2, q);
    const [a, b] = [linePoint(side, opening.s0, 0), linePoint(side, opening.s1, 0)];
    opening.item = add({ key: `opening:${i}`, name: o.name, kind: MODEL_KINDS[o.kind], group: o.kind === "vent" ? "Vents" : "Doors and windows", where: `${o.room}, ${o.wall} wall`, known: o.known, level: p.elevation, bbox: [Math.min(a[0], b[0]), Math.min(a[1], b[1]), z0, Math.max(a[0], b[0]), Math.max(a[1], b[1]), opening.z1] });
    return [opening];
  });
  const inRoom = (thing, i, prefix, kind, group) => {
    const q = thing.room != null ? named(thing.room) : null;
    if (thing.room != null && !q) return [];
    const first = q?.pieces[0];
    const [x, y] = first ? [first.x + thing.x, first.y + thing.y] : [thing.x, thing.y];
    // The piece it stands in: the one its rectangle is in, else the first.
    const within = (o) => o.x - 1e-6 <= x && x + thing.width <= o.x + o.width + 1e-6 && o.y - 1e-6 <= y && y + thing.depth <= o.y + o.depth + 1e-6;
    const p = q ? q.pieces.find(within) ?? first : null;
    const z = p ? p.elevation : details.grade;
    const resolved = { ...thing, place: q, piece: p, x0: x, y0: y, x1: x + thing.width, y1: y + thing.depth, z0: z + (thing.lift ?? 0) };
    resolved.z1 = resolved.z0 + (thing.height ?? MODEL_CLOSET_HEIGHT);
    resolved.item = add({ key: `${prefix}:${i}`, name: thing.name, kind, group, where: q ? q.name : "Outside", known: thing.known, level: p ? p.elevation : null, bbox: [x, y, resolved.z0, resolved.x1, resolved.y1, resolved.z1] });
    return [resolved];
  };
  const closets = details.closets.flatMap((c, i) => inRoom(c, i, "closet", "Closet", "Closets"));
  // A closet set into its room's wall opens through a door in that wall, which is drawn as its doors.
  for (const c of closets) {
    const p = c.piece;
    const edge = { back: [c.y0, p.y], front: [c.y1, p.y + p.depth], left: [c.x0, p.x], right: [c.x1, p.x + p.width] }[c.door];
    if (Math.abs(edge[0] - edge[1]) > 1e-6) continue;
    const [lo, hi] = c.door === "back" || c.door === "front" ? [c.x0, c.x1] : [c.y0, c.y1];
    for (const o of openings) {
      if (o.place === c.place && o.wall === c.door && o.kind === "door" && o.s0 >= lo - 1e-6 && o.s1 <= hi + 1e-6) o.closet = true;
    }
  }
  const fixtures = details.fixtures.flatMap((f, i) => inRoom(f, i, "fixture", MODEL_KINDS[f.kind], { vent: "Vents", plumbing: "Plumbing", fireplace: "Fireplaces", "half wall": "Half walls" }[f.kind] ?? "Appliances"));
  const grade = details.grade;
  const outside = (key, thing, kind, group, bbox, more = {}) => add({ key, name: thing.name, kind, group, where: "Outside", known: thing.known, level: null, bbox, ...more });
  const grounds = details.grounds.map((g, i) => ({ ...g, item: outside(`ground:${i}`, g, MODEL_KINDS[g.kind], "Yard", [g.x, g.y, grade, g.x + g.width, g.y + g.depth, grade + g.height]) }));
  // Trees, like roofs, come off when the house is cut away, so they don't hide it.
  const trees = details.trees.map((t, i) => ({ ...t, item: outside(`tree:${i}`, t, MODEL_KINDS[t.kind], "Yard", [t.x - t.spread / 2, t.y - t.spread / 2, grade, t.x + t.spread / 2, t.y + t.spread / 2, grade + t.height], { whole: true }) }));
  // A shed's doors and windows are the openings naming it, placed on its walls as a room's are, from the ground.
  const sheds = details.sheds.map((b, i) => {
    const item = outside(`shed:${i}`, b, "Shed", "Yard", [b.x, b.y, grade, b.x + b.width, b.y + b.depth, grade + b.peak]);
    const openings = details.openings.flatMap((o, k) => {
      if (o.room !== b.name || named(o.room)) return [];
      const side = SIDE_LINES[o.wall](b);
      const [s0, z0] = [side.a + o.offset, grade + o.sill];
      const [p, q] = [linePoint(side, s0, 0), linePoint(side, s0 + o.width, 0)];
      const bbox = [Math.min(p[0], q[0]), Math.min(p[1], q[1]), z0, Math.max(p[0], q[0]), Math.max(p[1], q[1]), z0 + o.height];
      const more = { group: o.kind === "vent" ? "Vents" : "Doors and windows", where: `${b.name}, ${o.wall} wall` };
      return [{ ...o, ...side, s0, s1: s0 + o.width, z0, z1: z0 + o.height, item: outside(`opening:${k}`, o, MODEL_KINDS[o.kind], null, bbox, more) }];
    });
    return { ...b, item, openings };
  });
  const fences = details.fences.map((f, i) => {
    const [xs, ys] = [f.points.map((p) => p[0]), f.points.map((p) => p[1])];
    const item = outside(`fence:${i}`, f, f.kind === "chain link" ? "Chain-link fence" : "Fence", "Yard", [Math.min(...xs), Math.min(...ys), grade, Math.max(...xs), Math.max(...ys), grade + f.height]);
    const gates = (f.gates ?? []).map(([x, y, width], k) => {
      const gate = { name: `Gate in the ${f.name.toLowerCase()}`, known: f.known };
      return { x, y, width, item: outside(`gate:${i}:${k}`, gate, "Gate", "Yard", [x - width / 2, y - width / 2, grade, x + width / 2, y + width / 2, grade + f.height]) };
    });
    return { ...f, item, gates };
  });
  const roofs = details.roofs.map((r, i) => ({ ...r, item: add({ key: `roof:${i}`, name: r.name, kind: "Roof", group: "Roof", where: "Roof", known: r.known, level: null, whole: true, bbox: [r.x, r.y, r.eave - MODEL_ROOF_DEPTH, r.x + r.width, r.y + r.depth, r.peak] }) }));
  const roofVents = details.roof_vents.flatMap((v, i) => {
    const under = roofs.filter((r) => onRoof(r, v.x, v.y));
    if (!under.length) return [];
    const z = Math.max(...under.map((r) => roofTop(r, v.x, v.y)));
    return [{ ...v, z, item: add({ key: `vent:${i}`, name: v.name, kind: "Roof vent", group: "Roof", where: "Roof", known: v.known, level: null, whole: true, bbox: [v.x - 0.2, v.y - 0.2, z, v.x + 0.2, v.y + 0.2, z + 1] }) }];
  });
  const lot = details.lot;
  const [lx0, ly0, lx1, ly1] = [Math.min(...lot.map((p) => p[0])), Math.min(...lot.map((p) => p[1])), Math.max(...lot.map((p) => p[0])), Math.max(...lot.map((p) => p[1]))];
  const lawn = add({ key: "lawn", name: "Lawn", kind: "Yard", where: "Outside", level: null, bbox: [lx0, ly0, grade, lx1, ly1, grade] });
  const cladding = Object.fromEntries(details.claddings.map((c) => [`${c.room}|${c.wall}`, c.finish]));

  const steps = details.steps.flatMap((t, i) => {
    const q = t.room != null ? named(t.room) : null;
    if (t.room != null && !q) return [];
    const bbox = [t.x, t.y, t.bottom, t.x + t.width, t.y + t.depth, t.top];
    return [{ ...t, item: add({ key: `steps:${i}`, name: t.name, kind: "Steps", group: "Steps", where: q ? q.name : "Outside", known: t.known, level: q ? q.pieces[0].elevation : null, bbox }) }];
  });

  // The levels, highest first, each at its highest floor's elevation: a garage a step down is on the main floor's.
  const levels = [...places.reduce((m, q) => m.set(q.floor, Math.max(m.get(q.floor) ?? -Infinity, q.pieces[0].elevation)), new Map())]
    .sort((a, b) => b[1] - a[1]);
  const bottom = Math.min(...pieces.map(([, p]) => p.base)) - 1.5;
  const high = Math.max(...items.map((item) => item.bbox[5]));
  const [x0, y0, x1, y1] = [Math.min(lx0, ...items.map((i) => i.bbox[0])), Math.min(ly0, ...items.map((i) => i.bbox[1])), Math.max(lx1, ...items.map((i) => i.bbox[3])), Math.max(ly1, ...items.map((i) => i.bbox[4]))];
  const radius = Math.hypot(x1 - x0, y1 - y0, high - bottom) / 2;
  // The house and its roofs, which the view frames until it's zoomed: in at `fit` times the whole scene.
  const house = [...places.map((q) => q.item), ...roofs.map((r) => r.item)].map((item) => item.bbox);
  const [hx0, hy0, hz0, hx1, hy1, hz1] = [0, 1, 2, 3, 4, 5].map((k) => (k < 3 ? Math.min : Math.max)(...house.map((b) => b[k])));
  return {
    details, places, pieces, openings, closets, fixtures, grounds, trees, sheds, fences, steps, roofs, roofVents, lot, lawn, cladding, items, levels, bottom, radius,
    center: [(x0 + x1) / 2, (y0 + y1) / 2, (bottom + high) / 2],
    houseCenter: [(hx0 + hx1) / 2, (hy0 + hy1) / 2, (hz0 + hz1) / 2],
    fit: Math.max(1, radius / (1.1 * (Math.hypot(hx1 - hx0, hy1 - hy0, hz1 - hz0) / 2))),
    // The theme's colors, but where the house has its own, like its siding.
    colors: Object.fromEntries(MODEL_TOKENS.map((token) => {
      const own = details.colors[token]?.[matchMedia("(prefers-color-scheme: dark)").matches ? 1 : 0];
      const hex = own ?? getComputedStyle(document.documentElement).getPropertyValue(`--model-${token}`).trim();
      return [token, [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)];
    })),
  };
}

// Whether an item shows when cut away to `mode`: a level, or "outside" for
// the whole house, with its roof and trees.
function modelShows(scene, mode) {
  const level = mode === "outside" ? Infinity : (scene.levels.find(([floor]) => floor === mode)?.[1] ?? Infinity);
  return (item) => (item.whole ? mode === "outside" : item.level == null || item.level <= level + 1e-6);
}

// The middle of an item, or of what shows of it when its walls are cut away.
function modelCenter(item, mode) {
  const [x0, y0, z0, x1, y1, z1] = item.bbox;
  const top = mode === "outside" || item.level == null ? z1 : Math.min(z1, item.level + MODEL_CUT);
  return [(x0 + x1) / 2, (y0 + y1) / 2, (z0 + top) / 2];
}

// --- Geometry: triangles, each vertex its position, normal, color and the id of what it's part of

function modelMesh() {
  const data = [];
  // A flat polygon, its normal (Newell's) pointing away from the center of the solid it's a face of.
  const face = (points, color, id, center) => {
    let [nx, ny, nz] = [0, 0, 0];
    points.forEach((p, i) => {
      const q = points[(i + 1) % points.length];
      nx += (p[1] - q[1]) * (p[2] + q[2]);
      ny += (p[2] - q[2]) * (p[0] + q[0]);
      nz += (p[0] - q[0]) * (p[1] + q[1]);
    });
    const length = Math.hypot(nx, ny, nz);
    if (length < 1e-9) return; // no area
    const mid = [0, 1, 2].map((k) => points.reduce((sum, p) => sum + p[k], 0) / points.length);
    const sign = (mid[0] - center[0]) * nx + (mid[1] - center[1]) * ny + (mid[2] - center[2]) * nz < 0 ? -1 : 1;
    const n = [nx, ny, nz].map((v) => (sign * v) / length);
    for (let i = 1; i < points.length - 1; i++) {
      for (const p of [points[0], points[i], points[i + 1]]) data.push(p[0], p[1], p[2], n[0], n[1], n[2], color[0], color[1], color[2], id);
    }
  };
  // A convex solid from its faces, each [points, color].
  const solid = (faces, id) => {
    const all = faces.flatMap(([points]) => points);
    const center = [0, 1, 2].map((k) => all.reduce((sum, p) => sum + p[k], 0) / all.length);
    for (const [points, color] of faces) face(points, color, id, center);
  };
  return { data, face, solid };
}

// Colors for a six-sided solid's faces: one for all, or {top, bottom, sides: [four]}.
function faceColors(colors) {
  return Array.isArray(colors) && typeof colors[0] === "number" ? { top: colors, bottom: colors, sides: [colors, colors, colors, colors] } : colors;
}

// A six-sided solid from its four bottom corners, in order around, and the four top corners over them.
function hexa(mesh, bottom, top, colors, id) {
  const c = faceColors(colors);
  const faces = [[bottom, c.bottom], [top, c.top]];
  for (let i = 0; i < 4; i++) faces.push([[bottom[i], bottom[(i + 1) % 4], top[(i + 1) % 4], top[i]], c.sides[i]]);
  mesh.solid(faces, id);
}

// sides: back, right, front, left
function box(mesh, [x0, y0, z0], [x1, y1, z1], colors, id) {
  hexa(mesh, [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0]], [[x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]], colors, id);
}

// A piece of wall, or something set in it: from s0 to s1 along the side's
// line, d0 to d1 inward of it, from zb up to h0 at s0 and h1 at s1. Its sides
// are its outside face, its s1 end, its inside face and its s0 end.
function slab(mesh, side, [s0, s1], [d0, d1], zb, [h0, h1], colors, id) {
  const at = (s, d, z) => [...linePoint(side, s, d), z];
  hexa(mesh, [at(s0, d0, zb), at(s1, d0, zb), at(s1, d1, zb), at(s0, d1, zb)], [at(s0, d0, h0), at(s1, d0, h1), at(s1, d1, h1), at(s0, d1, h0)], colors, id);
}

// A run of wall, from s0 to s1 and up from base to its top: raw0 to raw1, a
// straight line along it, but no higher than cap. It's cut into cells at the
// edges of its holes and at the heights in zs, each with its outside face in
// outer(z) at its height, its inside face in inner, and its top dark where
// the wall stops, or trim as the sill under a hole: the floor's color for a
// hole down to the floor at floorZ, as a doorway's threshold, which lies in
// the floor's plane, so the two look alike. A top under a floor above, which
// lies in that floor's plane, is that floor's color, topColor, for the same
// reason, where a doorway above bares it. Its ends are inner too,
// but for an end at its room's corner, end0(z) or end1(z), which is part of
// the wall around the corner's outside face.
function wallRun(mesh, C, w, id) {
  const raw = (s) => w.raw0 + ((w.raw1 - w.raw0) * (s - w.s0)) / (w.s1 - w.s0);
  const top = (s) => Math.min(raw(s), w.cap);
  const highest = Math.max(top(w.s0), top(w.s1));
  const zs = [...new Set([w.base, highest, w.cap, ...w.zs, ...w.holes.flatMap((h) => [h.z0, h.z1])])]
    .filter((z) => z >= w.base - 1e-9 && z <= highest + 1e-9)
    .sort((a, b) => a - b);
  const ss = new Set([w.s0, w.s1, ...w.holes.flatMap((h) => [h.s0, h.s1])]);
  if (Math.abs(w.raw1 - w.raw0) > 1e-9) {
    for (const z of zs) ss.add(w.s0 + ((z - w.raw0) * (w.s1 - w.s0)) / (w.raw1 - w.raw0)); // where the top crosses each height
  }
  const sList = [...ss].filter((s) => s >= w.s0 - 1e-9 && s <= w.s1 + 1e-9).sort((a, b) => a - b);
  for (let i = 0; i < sList.length - 1; i++) {
    const [sa, sb] = [sList[i], sList[i + 1]];
    if (sb - sa < 1e-6) continue;
    const [ta, tb] = [top(sa), top(sb)];
    for (let j = 0; j < zs.length - 1; j++) {
      const [za, zc] = [zs[j], zs[j + 1]];
      const [ha, hb] = [Math.min(zc, ta), Math.min(zc, tb)];
      if (ha <= za + 1e-6 && hb <= za + 1e-6) continue; // above the wall's top
      const within = (h) => h.s0 <= sa + 1e-6 && sb <= h.s1 + 1e-6;
      if (w.holes.some((h) => within(h) && h.z0 <= za + 1e-6 && zc <= h.z1 + 1e-6)) continue;
      const atTop = ha >= ta - 1e-6 && hb >= tb - 1e-6;
      const underFloor = atTop && w.topColor && ha >= raw(sa) - 1e-6 && hb >= raw(sb) - 1e-6; // not cut away
      const sill = w.holes.some((h) => within(h) && Math.abs(h.z0 - zc) < 1e-6);
      const threshold = sill && w.floorZ != null && Math.abs(zc - w.floorZ) < 1e-6;
      const z = (za + Math.min(ha, hb)) / 2;
      const end0 = sa <= w.s0 + 1e-6 && w.end0 ? w.end0(z) : w.inner;
      const end1 = sb >= w.s1 - 1e-6 && w.end1 ? w.end1(z) : w.inner;
      const colors = { bottom: w.inner, top: underFloor ? w.topColor : atTop ? C.cut : threshold ? w.floorColor : sill ? C.sash : w.inner, sides: [w.outer(z), end1, w.inner, end0] };
      slab(mesh, w, [sa, sb], [0, w.thick], za, [ha, hb], colors, id);
    }
  }
}

// Whether a wall stands whole when cut away: an outside wall at the back as
// seen, with none of the house beyond it to hide, at s along its side's line.
function standsWhole(scene, line, s, view) {
  const outward = line.axis === "x" ? [0, -line.k] : [-line.k, 0];
  if (outward[0] * view[0] + outward[1] * view[1] > 1e-6) return false; // it faces the viewer
  const [x, y] = linePoint(line, s, -0.01);
  return !scene.pieces.some(([, o]) => {
    if (line.axis === "x") return o.x < x && x < o.x + o.width && (outward[1] < 0 ? o.y < y : o.y + o.depth > y);
    return o.y < y && y < o.y + o.depth && (outward[0] < 0 ? o.x < x : o.x + o.width > x);
  });
}

function placeWalls(mesh, scene, q, p, mode, view) {
  const C = scene.colors;
  const inner = C[q.finish.walls];
  const covers = (spans, s) => spans.some(([a, b]) => a - 1e-6 <= s && s <= b + 1e-6);
  for (const [side, sideLine] of Object.entries(SIDE_LINES)) {
    const line = sideLine(p);
    for (let [a, b] of p.walls[side]) {
      if (line.axis === "y") { // left and right walls end at the back and front walls, which run past them
        if (Math.abs(a - p.y) < 1e-6 && covers(p.walls.back, line.line)) a += MODEL_WALL;
        if (Math.abs(b - (p.y + p.depth)) < 1e-6 && covers(p.walls.front, line.line)) b -= MODEL_WALL;
      }
      const edges = scene.pieces.filter(([o]) => o !== q).flatMap(([, o]) => (line.axis === "x" ? [o.x, o.x + o.width] : [o.y, o.y + o.depth]));
      const ss = [...new Set([a, b, ...edges.filter((s) => s > a && s < b)])].sort((u, v) => u - v);
      for (let i = 0; i < ss.length - 1; i++) {
        const [sa, sb] = [ss[i], ss[i + 1]];
        const [fx, fy] = linePoint(line, (sa + sb) / 2, -0.01); // just past the wall
        const occupant = (z) => occupiedAt(scene.pieces, fx, fy, z, q);
        const whole = mode === "outside" || (!occupant(p.elevation + 2) && standsWhole(scene, line, (sa + sb) / 2, view));
        const raw = (s) => p.top(...linePoint(line, s, 0));
        const zs = [p.elevation];
        const beyond = (x, y) => {
          for (const [o, b2] of scene.pieces) {
            if (o !== q && b2.x < x && x < b2.x + b2.width && b2.y < y && y < b2.y + b2.depth) zs.push(b2.base, b2.top(x, y));
          }
        };
        beyond(fx, fy);
        const highest = Math.max(raw(sa), raw(sb));
        const holes = scene.openings
          .filter((o) => o.through && o.axis === line.axis && Math.abs(o.line - line.line) < 1e-6)
          .filter((o) => o.s0 < sb && sa < o.s1 && o.z0 < highest && p.base < o.z1)
          .map((o) => ({ s0: Math.max(o.s0, sa), s1: Math.min(o.s1, sb), z0: o.z0, z1: o.z1 }));
        // The outside face of the wall on a side, at height z, with x, y just past it.
        const outside = (wallSide, x, y) => (z) => {
          if (occupiedAt(scene.pieces, x, y, z, q)) return inner;
          if (z < p.elevation) return p.ground ? C.foundation : C.siding;
          return q.finish.walls === "concrete" ? C.foundation : C[scene.cladding[`${q.name}|${wallSide}`]] ?? C.siding;
        };
        // An end at the room's corner is part of the outside face of the wall around the corner, so it's
        // colored as that is just past it, across the middle of this wall.
        const corner = (s, [endSide, at]) => {
          if (Math.abs(s - at) > 1e-6) return null;
          const out = endSide === "left" || endSide === "back" ? -0.01 : 0.01;
          const middle = line.line + (line.k * MODEL_WALL) / 2;
          const [x, y] = line.axis === "x" ? [at + out, middle] : [middle, at + out];
          beyond(x, y);
          return outside(endSide, x, y);
        };
        const ends = line.axis === "x" ? [["left", p.x], ["right", p.x + p.width]] : [["back", p.y], ["front", p.y + p.depth]];
        const [end0, end1] = [corner(sa, ends[0]), corner(sb, ends[1])];
        const outer = outside(side, fx, fy);
        // A floor over the wall's top, as a room's over the crawlspace under it.
        const [mx, my] = linePoint(line, (sa + sb) / 2, MODEL_WALL / 2);
        const over = scene.pieces.find(([o, b2]) => o !== q && b2.x < mx && mx < b2.x + b2.width && b2.y < my && my < b2.y + b2.depth
          && Math.abs(b2.elevation - raw(sa)) < 1e-6 && Math.abs(b2.elevation - raw(sb)) < 1e-6);
        const topColor = over ? C[over[0].finish.floor] : null;
        wallRun(mesh, C, { ...line, thick: MODEL_WALL, s0: sa, s1: sb, base: p.base, raw0: raw(sa), raw1: raw(sb), cap: whole ? Infinity : p.elevation + MODEL_CUT, zs, holes, inner, outer, end0, end1, floorZ: p.elevation, floorColor: C[q.finish.floor], topColor }, q.item.id);
      }
    }
  }
  // The floor, its edges just inside the walls it has.
  const e = (side) => (p.walls[side].length ? 0.01 : 0);
  const sides = Array(4).fill(inner);
  const floor = [p.x + e("left"), p.y + e("back"), p.x + p.width - e("right"), p.y + p.depth - e("front")];
  for (const [x0, y0, x1, y1] of stairwells(scene, p, floor)) {
    box(mesh, [x0, y0, p.elevation - 0.25], [x1, y1, p.elevation], { top: C[q.finish.floor], bottom: inner, sides }, q.item.id);
  }
}

// What fills a door or window from outside, or a vent's grilles, cut away with its wall.
function openingPanels(mesh, scene, o, mode, view) {
  const C = scene.colors;
  const t = MODEL_WALL;
  const whole = mode === "outside" || (o.exterior && standsWhole(scene, o, (o.s0 + o.s1) / 2, view));
  const cap = whole ? Infinity : o.piece.elevation + MODEL_CUT;
  const [s0, s1, z0, z1] = [o.s0, o.s1, o.z0, Math.min(o.z1, cap)];
  if (z1 <= z0 + 1e-6) return;
  const part = (s, d, [za, zb], color) => slab(mesh, o, s, d, za, [zb, zb], color, o.item.id);
  if (o.kind === "vent") {
    part([s0, s1], [t, t + 0.04], [z0, z1], C.grille);
    part([s0, s1], [-0.04, 0], [z0, z1], C.grille);
    return;
  }
  if (o.closet) { // a closet's doors, set into the wall, meeting in the middle
    part([s0, s1], [0.35 * t, 0.65 * t], [z0, z1], C.white);
    part([(s0 + s1) / 2 - 0.03, (s0 + s1) / 2 + 0.03], [0.3 * t, 0.7 * t], [z0, z1 - 0.01], C.seam); // just under their tops
    return;
  }
  if (o.kind === "opening") return; // a doorway with no door stands open
  if (!o.exterior) { // a door inside, closed, in white
    if (o.kind === "door") part([s0, s1], [0.35 * t, 0.65 * t], [z0, z1], C.white);
    return;
  }
  doorOrWindow(part, C, o, [s0, s1, z0, z1], (f) => f * t);
}

// An outside door or window from s0 to s1 and z0 to z1, its parts at depths d(f), for f from 0 at the wall's
// outside face to 1 at its inside: a door's panel, a garage door's with its seams, or a window's or sliding
// door's glass in a frame, split down the middle if it's wide. The glass stops just inside the frame's edges,
// so the two never share a face, which would flicker where it shows: atop a level cut away, or on a shed.
function doorOrWindow(part, C, o, [s0, s1, z0, z1], d) {
  const across = (f0, f1) => [d(f0), d(f1)];
  if (o.kind === "door" || o.kind === "garage door") {
    part([s0, s1], across(0.35, 0.65), [z0, z1], o.kind === "door" ? C.door : C["garage-door"]);
    for (const f of o.kind === "garage door" ? [0.25, 0.5, 0.75] : []) { // its panels' seams
      const z = z0 + f * o.height;
      if (z + 0.04 < z1) part([s0 + 0.2, s1 - 0.2], across(0.3, 0.35), [z - 0.04, z + 0.04], C.seam);
    }
    return;
  }
  const [bar, frame] = [0.15, across(0.2, 0.8)];
  part([s0 + 0.01, s1 - 0.01], across(0.45, 0.55), [z0 + 0.01, z1 - 0.01], C.glass);
  part([s0, s0 + bar], frame, [z0, z1], C.sash);
  part([s1 - bar, s1], frame, [z0, z1], C.sash);
  part([s0, s1], frame, [z0, z0 + bar], C.sash);
  if (z1 >= o.z1 - 1e-6) part([s0, s1], frame, [z1 - bar, z1], C.sash);
  const mid = (s0 + s1) / 2;
  if (s1 - s0 > 3.5) part([mid - bar / 2, mid + bar / 2], frame, [z0, z1], C.sash);
}

// A floor's rectangle, x0, y0, x1, y1, in cells around its stairwells: wherever a flight in a room climbs to
// this floor from below.
function stairwells(scene, p, [x0, y0, x1, y1]) {
  const holes = scene.steps
    .filter((t) => t.room != null && Math.abs(t.top - p.elevation) < 1e-6 && t.bottom < t.top)
    .map((t) => [Math.max(t.x, x0), Math.max(t.y, y0), Math.min(t.x + t.width, x1), Math.min(t.y + t.depth, y1)])
    .filter(([a, b, c, d]) => c > a + 1e-6 && d > b + 1e-6);
  if (!holes.length) return [[x0, y0, x1, y1]];
  const cuts = (lo, hi, edges) => [...new Set([lo, hi, ...edges.filter((v) => v > lo && v < hi)])].sort((u, v) => u - v);
  const [xs, ys] = [cuts(x0, x1, holes.flatMap((h) => [h[0], h[2]])), cuts(y0, y1, holes.flatMap((h) => [h[1], h[3]]))];
  const cells = [];
  for (let i = 0; i + 1 < xs.length; i++) {
    for (let j = 0; j + 1 < ys.length; j++) {
      const [x, y] = [(xs[i] + xs[i + 1]) / 2, (ys[j] + ys[j + 1]) / 2];
      if (!holes.some(([a, b, c, d]) => a < x && x < c && b < y && y < d)) cells.push([xs[i], ys[j], xs[i + 1], ys[j + 1]]);
    }
  }
  return cells;
}

// A closet's walls, but where it's against its room's, with doors across one side.
function closetWalls(mesh, scene, c, mode) {
  const C = scene.colors;
  const [p, t] = [c.piece, MODEL_CLOSET_WALL];
  const sides = {
    back: { axis: "x", line: c.y0, k: 1, a: c.x0, b: c.x1, against: c.y0 <= p.y + 1e-6 },
    front: { axis: "x", line: c.y1, k: -1, a: c.x0, b: c.x1, against: c.y1 >= p.y + p.depth - 1e-6 },
    left: { axis: "y", line: c.x0, k: 1, a: c.y0, b: c.y1, against: c.x0 <= p.x + 1e-6 },
    right: { axis: "y", line: c.x1, k: -1, a: c.y0, b: c.y1, against: c.x1 >= p.x + p.width - 1e-6 },
  };
  const paint = C[c.place.finish.walls];
  const cap = Math.min(p.elevation + MODEL_CLOSET_HEIGHT, mode === "outside" ? Infinity : p.elevation + MODEL_CUT);
  for (const [side, w] of Object.entries(sides)) {
    if (w.against) continue;
    // Inside the room's walls, and the left and right ones between the closet's back and front.
    let [a, b] = w.axis === "x" ? [Math.max(w.a, p.x + MODEL_WALL), Math.min(w.b, p.x + p.width - MODEL_WALL)] : [Math.max(w.a, p.y + MODEL_WALL), Math.min(w.b, p.y + p.depth - MODEL_WALL)];
    if (w.axis === "y" && !sides.back.against) a = Math.max(a, w.a + t);
    if (w.axis === "y" && !sides.front.against) b = Math.min(b, w.b - t);
    const raw = (s) => p.top(...linePoint(w, s, 0));
    const doors = { s0: a + 0.3, s1: b - 0.3, z0: p.elevation, z1: p.elevation + MODEL_CLOSET_DOOR };
    const holes = side === c.door ? [doors] : [];
    wallRun(mesh, C, { ...w, thick: t, s0: a, s1: b, base: p.elevation, raw0: raw(a), raw1: raw(b), cap, zs: [], holes, inner: paint, outer: () => paint }, c.item.id);
    if (side === c.door) {
      const z1 = Math.min(doors.z1, cap);
      slab(mesh, w, [doors.s0, doors.s1], [0.3 * t, 0.7 * t], doors.z0, [z1, z1], C.white, c.item.id);
      const mid = (doors.s0 + doors.s1) / 2; // where the two doors meet
      slab(mesh, w, [mid - 0.03, mid + 0.03], [0.25 * t, 0.75 * t], doors.z0, [z1 - 0.01, z1 - 0.01], C.seam, c.item.id); // just under their tops
    }
  }
}

// A polygon's area, positive if its corners go counterclockwise with y up.
function polygonArea(points) {
  return points.reduce((sum, [x1, y1], i) => {
    const [x2, y2] = points[(i + 1) % points.length];
    return sum + (x1 * y2 - x2 * y1) / 2;
  }, 0);
}

// The part of a polygon inside a rectangle (Sutherland-Hodgman).
function clipToRect(points, x0, y0, x1, y1) {
  let out = points;
  for (const [k, v, sign] of [[0, x0, 1], [0, x1, -1], [1, y0, 1], [1, y1, -1]]) {
    const input = out;
    out = [];
    input.forEach((a, i) => {
      const b = input[(i + 1) % input.length];
      const [inA, inB] = [(a[k] - v) * sign >= -1e-9, (b[k] - v) * sign >= -1e-9];
      if (inA) out.push(a);
      if (inA !== inB) {
        const t = (v - a[k]) / (b[k] - a[k]);
        out.push([a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])]);
      }
    });
    if (!out.length) return [];
  }
  return out;
}

// A simple polygon cut into triangles, by clipping off its ears.
function triangulate(points) {
  let left = points.filter((p, i) => {
    const q = points[(i + 1) % points.length];
    return Math.hypot(p[0] - q[0], p[1] - q[1]) > 1e-9;
  });
  const sign = Math.sign(polygonArea(left));
  const turn = (a, b, c) => sign * ((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]));
  const triangles = [];
  while (left.length > 3) {
    const n = left.length;
    const ear = left.findIndex((b, i) => {
      const [a, c] = [left[(i + n - 1) % n], left[(i + 1) % n]];
      if (turn(a, b, c) < 0) return false; // a corner turning in
      return !left.some((p) => p !== a && p !== b && p !== c && turn(a, b, p) >= 0 && turn(b, c, p) >= 0 && turn(c, a, p) >= 0);
    });
    if (ear < 0) break; // not a simple polygon after all
    const [a, b, c] = [left[(ear + n - 1) % n], left[ear], left[(ear + 1) % n]];
    if (Math.abs(turn(a, b, c)) > 1e-12) triangles.push([a, b, c]); // a straight corner just goes
    left = left.filter((_, i) => i !== ear);
  }
  if (left.length === 3) triangles.push(left);
  return triangles;
}

// The ground: the lot as a block of earth in its outline, lawn on top but
// where the house, porches and paving are, stepping down under the house to
// its lowest walls. Where a level is cut away with nothing under it, its
// footprint shows as slab. Paving that runs past the lot stops at its edge.
function earth(mesh, scene, shows) {
  const C = scene.colors;
  const [{ grade }, lot, b] = [scene.details, scene.lot, scene.bottom];
  const [lx0, ly0, , lx1, ly1] = scene.lawn.bbox;
  const rects = [...scene.pieces.map(([, p]) => p), ...scene.grounds.filter((g) => !g.outline)];
  const breaks = (lo, hi, edges) => [...new Set([lo, hi, ...edges.map((v) => Math.min(Math.max(v, lo), hi))])].sort((u, v) => u - v);
  const cutouts = scene.grounds.flatMap((g) => g.cutouts ?? []);
  const xs = breaks(lx0, lx1, [...rects.flatMap((r) => [r.x, r.x + r.width]), ...cutouts.flatMap(([x, , w]) => [x, x + w]), ...lot.map((p) => p[0])]);
  const ys = breaks(ly0, ly1, [...rects.flatMap((r) => [r.y, r.y + r.depth]), ...cutouts.flatMap(([, y, , d]) => [y, y + d]), ...lot.map((p) => p[1])]);
  const inside = (r, x, y) => r.x < x && x < r.x + r.width && r.y < y && y < r.y + r.depth;
  const cells = xs.slice(0, -1).map((_, i) => ys.slice(0, -1).map((_, j) => {
    const shape = clipToRect(lot, xs[i], ys[j], xs[i + 1], ys[j + 1]);
    if (shape.length < 3 || Math.abs(polygonArea(shape)) < 1e-4) return null; // outside the lot
    const [x, y] = [(xs[i] + xs[i + 1]) / 2, (ys[j] + ys[j + 1]) / 2];
    const covering = scene.pieces.filter(([, p]) => inside(p, x, y));
    const shown = covering.filter(([q]) => shows(q.item));
    const cell = { shape, top: grade, color: C.lawn, id: scene.lawn.id };
    if (shown.length) return { ...cell, top: Math.min(grade, ...shown.map(([, p]) => p.base)), color: C.soil, id: shown[0][0].item.id };
    if (covering.length) return { ...cell, color: C.slab, id: covering[0][0].item.id };
    const ground = scene.grounds.find((g) => !g.outline && inside(g, x, y));
    if (!ground) return cell;
    const cut = (ground.cutouts ?? []).some(([cx, cy, w, d]) => inside({ x: cx, y: cy, width: w, depth: d }, x, y));
    return { ...cell, color: cut ? C.dirt : paving(C, ground), id: ground.item.id };
  }));
  cells.forEach((column, i) => column.forEach((cell, j) => {
    if (!cell) return;
    const [x0, x1, y0, y1, z] = [xs[i], xs[i + 1], ys[j], ys[j + 1], cell.top];
    const center = [(x0 + x1) / 2, (y0 + y1) / 2, z - 1];
    for (const triangle of triangulate(cell.shape)) mesh.face(triangle.map(([x, y]) => [x, y, z]), cell.color, cell.id, center);
    // Steps down to lower neighbors, under the house.
    const neighbors = [[i - 1, j, [[x0, y0], [x0, y1]]], [i + 1, j, [[x1, y0], [x1, y1]]], [i, j - 1, [[x0, y0], [x1, y0]]], [i, j + 1, [[x0, y1], [x1, y1]]]];
    for (const [ni, nj, [[ax, ay], [bx, by]]] of neighbors) {
      const low = cells[ni]?.[nj]?.top;
      if (low != null && low < z - 1e-6) mesh.face([[ax, ay, low], [bx, by, low], [bx, by, z], [ax, ay, z]], C.soil, scene.lawn.id, center);
    }
  }));
  // A flat ground cut to an outline lies just over the lawn.
  for (const g of scene.grounds.filter((g) => g.outline && !(g.height > 0))) {
    const center = [g.x + g.width / 2, g.y + g.depth / 2, grade - 1];
    for (const triangle of triangulate(g.outline)) mesh.face(triangle.map(([x, y]) => [x, y, grade + 0.03]), paving(C, g), g.item.id, center);
  }
  // The lot's sides, down to its bottom.
  const sign = Math.sign(polygonArea(lot));
  lot.forEach(([ax, ay], i) => {
    const [bx, by] = lot[(i + 1) % lot.length];
    const length = Math.hypot(bx - ax, by - ay);
    const [ix, iy] = [(-sign * (by - ay)) / length, (sign * (bx - ax)) / length]; // inward
    const center = [(ax + bx) / 2 + ix, (ay + by) / 2 + iy, (grade + b) / 2];
    mesh.face([[ax, ay, b], [bx, by, b], [bx, by, grade], [ax, ay, grade]], C.soil, scene.lawn.id, center);
  });
  const middle = [(lx0 + lx1) / 2, (ly0 + ly1) / 2, b + 1];
  for (const triangle of triangulate(lot)) mesh.face(triangle.map(([x, y]) => [x, y, b]), C.soil, scene.lawn.id, middle);
}

// A vertical prism around x, y: n sides, radius r, from z0 to z1; a cone if `point`.
function round(mesh, [x, y], r, z0, z1, color, id, point = false, n = 8) {
  const ring = (z) => Array.from({ length: n }, (_, i) => [x + r * Math.cos((2 * Math.PI * i) / n), y + r * Math.sin((2 * Math.PI * i) / n), z]);
  const bottom = ring(z0);
  const faces = [[bottom, color]];
  const top = point ? null : ring(z1);
  if (top) faces.push([top, color]);
  for (let i = 0; i < n; i++) {
    const j = (i + 1) % n;
    faces.push([top ? [bottom[i], bottom[j], top[j], top[i]] : [bottom[i], bottom[j], [x, y, z1]], color]);
  }
  mesh.solid(faces, id);
}

// An icosahedron split once into 80 triangles, on the unit sphere.
const MODEL_BALL = (() => {
  const g = (1 + Math.sqrt(5)) / 2;
  const unit = (v) => v.map((c) => c / Math.hypot(...v));
  const corners = [[-1, g, 0], [1, g, 0], [-1, -g, 0], [1, -g, 0], [0, -1, g], [0, 1, g], [0, -1, -g], [0, 1, -g], [g, 0, -1], [g, 0, 1], [-g, 0, -1], [-g, 0, 1]].map(unit);
  const faces = [[0, 11, 5], [0, 5, 1], [0, 1, 7], [0, 7, 10], [0, 10, 11], [1, 5, 9], [5, 11, 4], [11, 10, 2], [10, 7, 6], [7, 1, 8], [3, 9, 4], [3, 4, 2], [3, 2, 6], [3, 6, 8], [3, 8, 9], [4, 9, 5], [2, 4, 11], [6, 2, 10], [8, 6, 7], [9, 8, 1]];
  return faces.flatMap(([a, b, c]) => {
    const [p, q, r] = [corners[a], corners[b], corners[c]];
    const mid = (u, v) => unit(u.map((x, k) => (x + v[k]) / 2));
    const [pq, qr, rp] = [mid(p, q), mid(q, r), mid(r, p)];
    return [[p, pq, rp], [pq, q, qr], [rp, qr, r], [pq, qr, rp]];
  });
})();

function tree(mesh, scene, t) {
  const [C, grade, id] = [scene.colors, scene.details.grade, t.item.id];
  round(mesh, [t.x, t.y], Math.max(0.35, t.spread * 0.03), grade, grade + t.height * 0.6, C.trunk, id);
  if (t.ring > 0) { // a raised ring of stone or brick, filled with mulch
    round(mesh, [t.x, t.y], t.ring, grade, grade + 0.9, C[t.edging] ?? C.stone, id, false, 16);
    round(mesh, [t.x, t.y], t.ring - 0.4, grade + 0.9, grade + 0.95, C.mulch, id, false, 16);
  }
  if (t.kind === "evergreen") {
    for (const [from, to, width] of [[0.18, 0.62, 1], [0.4, 0.82, 0.74], [0.6, 1, 0.48]]) {
      round(mesh, [t.x, t.y], (t.spread / 2) * width, grade + t.height * from, grade + t.height * to, C.needles, id, true, 10);
    }
    return;
  }
  // A leafy canopy, its facets a little lighter or darker in turn.
  const [rx, rz] = [t.spread / 2, t.spread * 0.35];
  const center = [t.x, t.y, grade + t.height - rz];
  MODEL_BALL.forEach((triangle, i) => {
    const shade = 0.92 + 0.16 * (((i * 37) % 11) / 10);
    const points = triangle.map(([x, y, z]) => [center[0] + x * rx, center[1] + y * rx, center[2] + z * rz]);
    mesh.face(points, C.leaves.map((c) => Math.min(1, c * shade)), id, center);
  });
}

// A section of roof, solid from its underside: a gable's five-sided profile,
// or a shed's four, run along its length. Its slopes in roofing, its gable
// ends and any tall high side in siding, its fascia and soffit in trim.
function roofSection(mesh, scene, r) {
  const C = scene.colors;
  if (r.outline) return roofOutline(mesh, scene, r);
  const low = r.underside ?? r.eave - MODEL_ROOF_DEPTH;
  const roofing = r.color ?? C.roof;
  const along = r.ridge ?? (r.drop === "left" || r.drop === "right" ? "y" : "x");
  const [u0, u1] = along === "x" ? [r.y, r.y + r.depth] : [r.x, r.x + r.width];
  const [a, b] = along === "x" ? [r.x, r.x + r.width] : [r.y, r.y + r.depth];
  let profile; // across the roof: [u, z] around its cross-section
  if (r.ridge) profile = [[u0, low], [u1, low], [u1, r.eave], [(u0 + u1) / 2, r.peak], [u0, r.eave]];
  else if (r.drop === "right" || r.drop === "front") profile = [[u0, low], [u1, low], [u1, r.eave], [u0, r.peak]];
  else profile = [[u0, low], [u1, low], [u1, r.peak], [u0, r.eave]];
  const at = (u, z, s) => (along === "x" ? [s, u, z] : [u, s, z]);
  const faces = [[profile.map(([u, z]) => at(u, z, a)), C.siding], [profile.map(([u, z]) => at(u, z, b)), C.siding]];
  profile.forEach(([u, z], i) => {
    const [u2, z2] = profile[(i + 1) % profile.length];
    const upright = Math.abs(u - u2) < 1e-9;
    const color = upright ? (Math.abs(z2 - z) > MODEL_ROOF_DEPTH + 0.05 ? C.siding : C.trim) : z === low && z2 === low ? C.trim : roofing;
    faces.push([[at(u, z, a), at(u2, z2, a), at(u2, z2, b), at(u, z, b)], color]);
  });
  mesh.solid(faces, r.item.id);
  // Trim along the sloped edges of its ends, just proud of them.
  const middle = at((u0 + u1) / 2, (low + r.peak) / 2, (a + b) / 2);
  profile.forEach(([u, z], i) => {
    const [u2, z2] = profile[(i + 1) % profile.length];
    if (Math.abs(u - u2) < 1e-9 || (z === low && z2 === low)) return; // not a slope
    for (const s of [a - 0.03, b + 0.03]) mesh.face([at(u, z, s), at(u2, z2, s), at(u2, z2 - 0.45, s), at(u, z - 0.45, s)], C.trim, r.item.id, middle);
  });
}

// What a porch, patio, deck, walk, bed or flight of steps is paved with.
function paving(C, g) {
  return { concrete: C.concrete, flagstone: C.flagstone, wood: C.deck, gravel: C.gravel, mulch: C.mulch, carpet: C.carpet }[g.paving] ?? C.concrete;
}

// A roof section cut to its outline: its slope over the outline, a flat
// underside, and upright sides, with trim along the sloped edges but those
// it shares with another section, where the two meet in a valley.
function roofOutline(mesh, scene, r) {
  const C = scene.colors;
  const low = r.underside ?? r.eave - MODEL_ROOF_DEPTH;
  const points = r.outline;
  const n = points.length;
  const top = points.map(([x, y]) => [x, y, roofTop(r, x, y)]);
  const bottom = points.map(([x, y]) => [x, y, low]);
  const faces = [[top, C.roof], [bottom, C.trim]];
  points.forEach((_, i) => {
    const j = (i + 1) % n;
    const tall = Math.max(top[i][2], top[j][2]) - low > MODEL_ROOF_DEPTH + 0.05;
    faces.push([[bottom[i], bottom[j], top[j], top[i]], tall ? C.siding : C.trim]);
  });
  mesh.solid(faces, r.item.id);
  const key = (p, q) => [p, q].map((v) => v.map((c) => c.toFixed(2)).join()).sort().join(" ");
  const shared = new Set(scene.roofs.filter((o) => o !== r && o.outline).flatMap((o) => o.outline.map((p, i) => key(p, o.outline[(i + 1) % o.outline.length]))));
  const sign = Math.sign(polygonArea(points));
  const middle = [points.reduce((t, p) => t + p[0], 0) / n, points.reduce((t, p) => t + p[1], 0) / n, (low + r.peak) / 2];
  points.forEach((p, i) => {
    const j = (i + 1) % n;
    if (Math.abs(top[i][2] - top[j][2]) < 1e-6 || shared.has(key(p, points[j]))) return; // level, or a valley
    const [ex, ey] = [points[j][0] - p[0], points[j][1] - p[1]];
    const length = Math.hypot(ex, ey);
    const [ox, oy] = [(sign * ey * 0.03) / length, (-sign * ex * 0.03) / length]; // just outside
    const at = (q, dz) => [q[0] + ox, q[1] + oy, q[2] + dz];
    mesh.face([at(top[i], 0), at(top[j], 0), at(top[j], -0.45), at(top[i], -0.45)], C.trim, r.item.id, middle);
  });
}

// A flight of steps: each a block from the bottom up to its tread, over its
// part of the flight and everything above it.
function stepsFlight(mesh, scene, t) {
  const C = scene.colors;
  const n = t.count ?? Math.max(1, Math.round((t.top - t.bottom) / 0.6));
  for (let k = 0; k < n; k++) {
    const f = k / n; // how far up the flight this step starts
    let [x0, y0, x1, y1] = [t.x, t.y, t.x + t.width, t.y + t.depth];
    if (t.up === "back") y1 -= f * t.depth;
    if (t.up === "front") y0 += f * t.depth;
    if (t.up === "left") x1 -= f * t.width;
    if (t.up === "right") x0 += f * t.width;
    box(mesh, [x0, y0, t.bottom], [x1, y1, t.bottom + ((k + 1) * (t.top - t.bottom)) / n], paving(C, t), t.item.id);
  }
}

// A raised ground: a block of its paving up to its top, over its rectangle or cut to its outline. With
// edging, its sides are a low wall of stone or brick 0.4 ft thick instead, with its paving filled in a
// little below the wall's top.
function raisedGround(mesh, scene, g) {
  const [C, grade, id] = [scene.colors, scene.details.grade, g.item.id];
  const top = grade + g.height;
  const shape = g.outline ?? [[g.x, g.y], [g.x + g.width, g.y], [g.x + g.width, g.y + g.depth], [g.x, g.y + g.depth]];
  const sign = Math.sign(polygonArea(shape));
  const fill = g.edging ? top - 0.1 : top;
  const below = [g.x + g.width / 2, g.y + g.depth / 2, grade - 1];
  for (const triangle of triangulate(shape)) mesh.face(triangle.map(([x, y]) => [x, y, fill]), paving(C, g), id, below);
  shape.forEach((a, i) => {
    const [b, c] = [shape[(i + 1) % shape.length], shape[(i + 2) % shape.length]];
    const length = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (length < 1e-9) return;
    const [dx, dy] = [(b[0] - a[0]) / length, (b[1] - a[1]) / length];
    const [ix, iy] = [-sign * dy, sign * dx]; // inward
    const at = (s, d, z) => [a[0] + dx * s + ix * d, a[1] + dy * s + iy * d, z];
    if (!g.edging) {
      mesh.face([at(0, 0, grade), at(length, 0, grade), at(length, 0, top), at(0, 0, top)], paving(C, g), id, at(length / 2, 1, grade));
      return;
    }
    // At a corner turning in, the wall runs on its thickness past it, to close the corner inside.
    const turnsIn = sign * ((b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])) < -1e-9;
    const [end, t] = [length + (turnsIn ? 0.4 : 0), 0.4];
    const corners = (z) => [at(0, 0, z), at(end, 0, z), at(end, t, z), at(0, t, z)];
    hexa(mesh, corners(grade), corners(top), C[g.edging] ?? C.stone, id);
  });
}

// A ground's railings, in its paving, 3 ft over its top along each side it lists: posts about every 6 ft, a
// top and a bottom rail, and balusters between them.
function railings(mesh, scene, g) {
  const color = paving(scene.colors, g);
  const [z0, z1] = [scene.details.grade + g.height, scene.details.grade + g.height + 3];
  for (const side of g.railings ?? []) {
    const across = side === "back" || side === "front"; // runs along x
    const [a0, a1] = across ? [g.x, g.x + g.width] : [g.y, g.y + g.depth];
    const line = { back: g.y + 0.15, front: g.y + g.depth - 0.15, left: g.x + 0.15, right: g.x + g.width - 0.15 }[side];
    const bar = (s0, s1, h0, h1, half) => box(mesh, across ? [s0, line - half, h0] : [line - half, s0, h0], across ? [s1, line + half, h1] : [line + half, s1, h1], color, g.item.id);
    bar(a0, a1, z1 - 0.2, z1, 0.15);
    bar(a0, a1, z0 + 0.25, z0 + 0.4, 0.08);
    const posts = Math.max(1, Math.round((a1 - a0) / 6));
    for (let k = 0; k <= posts; k++) {
      const s = a0 + ((a1 - a0) * k) / posts;
      bar(Math.max(a0, s - 0.15), Math.min(a1, s + 0.15), z0, z1, 0.15);
    }
    for (let s = a0 + 0.5; s < a1 - 0.3; s += 0.45) bar(s - 0.04, s + 0.04, z0 + 0.4, z1 - 0.2, 0.04);
  }
}

// A shed: walls in siding under a gable roof, its eaves a little past them, and its doors and windows.
function shed(mesh, scene, b) {
  const [C, grade, over] = [scene.colors, scene.details.grade, 0.5];
  box(mesh, [b.x, b.y, grade], [b.x + b.width, b.y + b.depth, grade + b.wall], C.siding, b.item.id);
  const span = b.ridge === "x" ? { x: b.x, y: b.y - over, width: b.width, depth: b.depth + 2 * over } : { x: b.x - over, y: b.y, width: b.width + 2 * over, depth: b.depth };
  roofSection(mesh, scene, { ...span, ridge: b.ridge, eave: grade + b.wall + MODEL_ROOF_DEPTH, peak: grade + b.peak, item: b.item, color: C["shed-roof"] });
  // Its doors and windows, standing just out from its walls.
  for (const o of b.openings) {
    const part = (s, d, [za, zb], color) => slab(mesh, o, s, d, za, [zb, zb], color, o.item.id);
    if (o.kind === "vent") part([o.s0, o.s1], [-0.04, 0], [o.z0, o.z1], C.grille);
    else if (o.kind === "opening") part([o.s0, o.s1], [-0.02, 0], [o.z0, o.z1], C.black); // a dark doorway
    else doorOrWindow(part, C, o, [o.s0, o.s1, o.z0, o.z1], (f) => (f - 1) * 0.12);
  }
}

// A board fence along each run between its points, with posts about every 8
// ft and either side of each gate, and its gates a shade darker.
// A fence: wood boards between posts, or chain link, steel posts with a top rail and its mesh, a diamond
// lattice of wires, between them. Each gate is a panel of the same between posts of its own.
function fence(mesh, scene, f) {
  const [C, grade, id] = [scene.colors, scene.details.grade, f.item.id];
  const chain = f.kind === "chain link";
  const color = chain ? C.steel : C.fence;
  const [post, gateColor] = [color.map((c) => c * 0.8), color.map((c) => c * 0.88)];
  const [half, postHalf, postTop] = chain ? [0.05, 0.1, 0.1] : [0.1, 0.2, 0.25]; // half its thickness, and its posts'
  f.points.slice(1).forEach((q, i) => {
    const p = f.points[i];
    const length = Math.hypot(q[0] - p[0], q[1] - p[1]);
    if (length < 1e-6) return;
    const [dx, dy] = [(q[0] - p[0]) / length, (q[1] - p[1]) / length];
    const at = (t, side, z) => [p[0] + dx * t - dy * half * side, p[1] + dy * t + dx * half * side, z]; // side: -1 or 1, across
    const run = (t0, t1, z0, z1, color, itemId) => {
      const corners = (z) => [at(t0, -1, z), at(t1, -1, z), at(t1, 1, z), at(t0, 1, z)];
      hexa(mesh, corners(z0), corners(z1), color, itemId);
    };
    // A wire slanting up from ta at za to tb at zb, its ends square to it, so where two wires meet, their ends
    // cross rather than lying in one plane.
    const wire = (ta, za, tb, zb, color, itemId) => {
      const k = 0.05 * Math.sign((zb - za) * (tb - ta)); // rising to the right, or to the left
      const corners = (t, z) => [at(t - 0.05, -1, z + k), at(t + 0.05, -1, z - k), at(t + 0.05, 1, z - k), at(t - 0.05, 1, z + k)];
      hexa(mesh, corners(ta, za), corners(tb, zb), color, itemId);
    };
    // Boards, or chain link: a rail along its top, and a gate's bottom and sides, with the mesh between.
    const panel = (t0, t1, z0, z1, color, itemId, gate = false) => {
      if (!chain) return run(t0, t1, z0, z1, color, itemId);
      const rail = 0.12;
      run(t0, t1, z1 - rail, z1, color, itemId);
      if (gate) for (const [a, b, c, d] of [[t0, t1, z0, z0 + rail], [t0, t0 + rail, z0, z1], [t1 - rail, t1, z0, z1]]) run(a, b, c, d, color, itemId);
      const [lo, hi] = [z0 + (gate ? rail : 0.15), z1 - rail];
      const h = hi - lo;
      for (let c = t0 - h; c < t1; c += 1) { // rising to the right: t = c + (z - lo)
        const [a, b] = [Math.max(c, t0), Math.min(c + h, t1)];
        if (b - a > 0.05) wire(a, lo + a - c, b, lo + b - c, color, itemId);
      }
      for (let c = t0; c < t1 + h; c += 1) { // rising to the left: t = c - (z - lo)
        const [a, b] = [Math.min(c, t1), Math.max(c - h, t0)];
        if (a - b > 0.05) wire(a, lo + c - a, b, lo + c - b, color, itemId);
      }
    };
    // This run's gates, as spans along it.
    const gates = f.gates.flatMap((g) => {
      const [t, off] = [(g.x - p[0]) * dx + (g.y - p[1]) * dy, Math.abs((g.x - p[0]) * dy - (g.y - p[1]) * dx)];
      const on = off < 0.5 && t >= g.width / 2 && t <= length - g.width / 2; // on this run, not just in line with it
      return on ? [{ t0: t - g.width / 2, t1: t + g.width / 2, item: g.item }] : [];
    }).sort((a, b) => a.t0 - b.t0);
    let t = 0;
    for (const g of gates) {
      if (g.t0 > t) panel(t, g.t0, grade, grade + f.height, color, id);
      panel(g.t0 + 0.2, g.t1 - 0.2, grade + 0.25, grade + f.height - 0.15, gateColor, g.item.id, true);
      t = g.t1;
    }
    if (t < length) panel(t, length, grade, grade + f.height, color, id);
    const posts = Math.max(1, Math.round(length / (chain ? 10 : 8)));
    const spots = Array.from({ length: posts + 1 }, (_, k) => (length * k) / posts).filter((s) => !gates.some((g) => g.t0 < s && s < g.t1));
    for (const s of [...spots, ...gates.flatMap((g) => [g.t0, g.t1])]) {
      const [x, y] = [p[0] + dx * s, p[1] + dy * s];
      // Its bottom a hair over the ground, a chain-link post's another, so it meets no panel's or other post's.
      const foot = grade + (chain ? 0.04 : 0.02);
      box(mesh, [x - postHalf, y - postHalf, foot], [x + postHalf, y + postHalf, grade + f.height + postTop], post, id);
    }
  });
}

// A fixture: a box in its finish. A fireplace's firebox is dark, a little proud of the side facing away from
// the wall it's nearest, across its middle and from low on it to a little over halfway up.
// A fixture: a box in its finish. A half wall is in its room's wall color, with a cap in its finish; a
// fireplace has a firebox on the side away from the wall it's against.
function fixture(mesh, C, f) {
  if (f.kind === "half wall") {
    const cap = Math.max(f.z0, f.z1 - 0.15);
    box(mesh, [f.x0, f.y0, f.z0], [f.x1, f.y1, cap], C[f.place?.finish.walls] ?? C.paint, f.item.id);
    box(mesh, [f.x0, f.y0, cap], [f.x1, f.y1, f.z1], C[f.finish] ?? C.white, f.item.id);
    return;
  }
  box(mesh, [f.x0, f.y0, f.z0], [f.x1, f.y1, f.z1], C[f.finish] ?? C.white, f.item.id);
  if (f.kind !== "fireplace" || !f.piece) return;
  const p = f.piece;
  const gaps = { left: f.x0 - p.x, right: p.x + p.width - f.x1, back: f.y0 - p.y, front: p.y + p.depth - f.y1 };
  const wall = Object.keys(gaps).reduce((a, b) => (gaps[b] < gaps[a] ? b : a));
  const middle = (a, b) => [a + 0.2 * (b - a), b - 0.2 * (b - a)];
  const [z0, z1] = [f.z0 + 0.15 * (f.z1 - f.z0), f.z0 + 0.6 * (f.z1 - f.z0)];
  const t = 0.03;
  if (wall === "left" || wall === "right") {
    const x = wall === "left" ? f.x1 : f.x0 - t;
    const [y0, y1] = middle(f.y0, f.y1);
    box(mesh, [x, y0, z0], [x + t, y1, z1], C.black, f.item.id);
  } else {
    const y = wall === "back" ? f.y1 : f.y0 - t;
    const [x0, x1] = middle(f.x0, f.x1);
    box(mesh, [x0, y, z0], [x1, y + t, z1], C.black, f.item.id);
  }
}

function buildModel(scene, mode, view) {
  const mesh = modelMesh();
  const C = scene.colors;
  const shows = modelShows(scene, mode);
  for (const q of scene.places) if (shows(q.item)) for (const p of q.pieces) placeWalls(mesh, scene, q, p, mode, view);
  for (const o of scene.openings) if (shows(o.item)) openingPanels(mesh, scene, o, mode, view);
  for (const c of scene.closets) if (shows(c.item)) closetWalls(mesh, scene, c, mode);
  for (const f of scene.fixtures) if (shows(f.item)) fixture(mesh, C, f);
  earth(mesh, scene, shows);
  for (const g of scene.grounds) {
    if (g.height > 0) raisedGround(mesh, scene, g);
    railings(mesh, scene, g);
  }
  for (const b of scene.sheds) shed(mesh, scene, b);
  for (const t of scene.steps) if (shows(t.item)) stepsFlight(mesh, scene, t);
  for (const f of scene.fences) fence(mesh, scene, f);
  if (mode === "outside") {
    for (const t of scene.trees) tree(mesh, scene, t);
    for (const r of scene.roofs) roofSection(mesh, scene, r);
    for (const v of scene.roofVents) box(mesh, [v.x - 0.2, v.y - 0.2, v.z - 0.5], [v.x + 0.2, v.y + 0.2, v.z + 1], C.black, v.item.id);
  }
  return new Float32Array(mesh.data);
}

// --- WebGL

const MODEL_VERTEX = `
attribute vec3 position;
attribute vec3 normal;
attribute vec3 color;
attribute float id;
uniform mat4 matrix;
uniform vec3 light;
uniform float focus;
uniform vec3 focusColor;
uniform float picking;
varying vec3 shade;
void main() {
  gl_Position = matrix * vec4(position, 1.0);
  if (picking > 0.5) {
    shade = vec3(mod(id, 256.0), mod(floor(id / 256.0), 256.0), floor(id / 65536.0)) / 255.0;
  } else {
    vec3 base = abs(id - focus) < 0.5 ? mix(color, focusColor, 0.55) : color;
    shade = base * (0.62 + 0.45 * max(dot(normal, light), 0.0));
  }
}`;

const MODEL_FRAGMENT = `
precision mediump float;
varying vec3 shade;
void main() {
  gl_FragColor = vec4(min(shade, vec3(1.0)), 1.0);
}`;

function modelGL(canvas) {
  const gl = canvas.getContext("webgl", { antialias: true });
  if (!gl) return null;
  const shader = (type, source) => {
    const s = gl.createShader(type);
    gl.shaderSource(s, source);
    gl.compileShader(s);
    return s;
  };
  const program = gl.createProgram();
  gl.attachShader(program, shader(gl.VERTEX_SHADER, MODEL_VERTEX));
  gl.attachShader(program, shader(gl.FRAGMENT_SHADER, MODEL_FRAGMENT));
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return null;
  gl.useProgram(program);
  const buffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  [["position", 3, 0], ["normal", 3, 3], ["color", 3, 6], ["id", 1, 9]].forEach(([name, size, offset]) => {
    const at = gl.getAttribLocation(program, name);
    gl.enableVertexAttribArray(at);
    gl.vertexAttribPointer(at, size, gl.FLOAT, false, 40, offset * 4);
  });
  const uniforms = Object.fromEntries(["matrix", "light", "focus", "focusColor", "picking"].map((u) => [u, gl.getUniformLocation(program, u)]));
  gl.enable(gl.DEPTH_TEST);
  return { gl, uniforms, count: 0, built: null, pick: null };
}

// The camera for the 3D view's turn and tilt, centered on `target`: the
// directions across and up the screen and toward the viewer, and the matrix
// from feet to clip space, its depth always across the whole scene.
function modelCamera(scene, width, height, scale, target) {
  const [turn, tilt] = [state.mapAngle, state.mapTilt].map((d) => (d * Math.PI) / 180);
  const toward = [Math.sin(turn), Math.cos(turn)];
  const [cos, sin] = [Math.cos(tilt), Math.sin(tilt)];
  const view = [toward[0] * cos, toward[1] * cos, sin].map((v) => (Math.abs(v) < 1e-9 ? 0 : v));
  const across = [toward[1], -toward[0], 0];
  const up = [-toward[0] * sin, -toward[1] * sin, cos];
  const c = target;
  const dot = (u, v) => u[0] * v[0] + u[1] * v[1] + u[2] * v[2];
  const [kx, ky, kz] = [(2 * scale) / width, (2 * scale) / height, -1 / scene.radius];
  const rows = [[across, kx, c], [up, ky, c], [view, kz, scene.center]].map(([v, k, from]) => [...v.map((x) => x * k), -dot(v, from) * k]);
  const matrix = new Float32Array(16);
  for (let col = 0; col < 4; col++) {
    for (let row = 0; row < 3; row++) matrix[col * 4 + row] = rows[row][col];
  }
  matrix[15] = 1;
  // Light from over the viewer's left shoulder, so faces turned to the left are lighter, as in the 3D view.
  const light = [0, 1, 2].map((k) => 0.5 * view[k] + 0.55 * up[k] - 0.45 * across[k]);
  const norm = Math.hypot(...light);
  return {
    view, matrix,
    light: light.map((v) => v / norm),
    project: ([x, y, z]) => {
      const d = [x - c[0], y - c[1], z - c[2]];
      return [width / 2 + dot(across, d) * scale, height / 2 - dot(up, d) * scale];
    },
  };
}

function renderModel(m, camera, focus, picking) {
  const { gl, uniforms: u } = m;
  gl.uniformMatrix4fv(u.matrix, false, camera.matrix);
  gl.uniform3fv(u.light, camera.light);
  gl.uniform1f(u.focus, focus ?? -1);
  gl.uniform3fv(u.focusColor, m.focusColor);
  gl.uniform1f(u.picking, picking ? 1 : 0);
  gl.clearColor(0, 0, 0, 0);
  gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
  gl.drawArrays(gl.TRIANGLES, 0, m.count);
}

// The id of what's drawn at a pixel, from a render of ids to a buffer of their own.
function pickModel(m, camera, x, y) {
  const { gl } = m;
  const [w, h] = [gl.drawingBufferWidth, gl.drawingBufferHeight];
  if (!m.pick || m.pick.w !== w || m.pick.h !== h) {
    const texture = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    const depth = gl.createRenderbuffer();
    gl.bindRenderbuffer(gl.RENDERBUFFER, depth);
    gl.renderbufferStorage(gl.RENDERBUFFER, gl.DEPTH_COMPONENT16, w, h);
    const buffer = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, buffer);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, texture, 0);
    gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.RENDERBUFFER, depth);
    m.pick = { w, h, buffer };
  }
  gl.bindFramebuffer(gl.FRAMEBUFFER, m.pick.buffer);
  renderModel(m, camera, null, true);
  const pixel = new Uint8Array(4);
  const ratio = w / gl.canvas.clientWidth;
  gl.readPixels(Math.floor(x * ratio), Math.floor(h - y * ratio), 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
  gl.bindFramebuffer(gl.FRAMEBUFFER, null);
  return pixel[0] + pixel[1] * 256 + pixel[2] * 65536;
}

// --- The view on the map

// Draw the detailed view at this width, returning the map's layout: its
// height, the keys arrow keys step through, and how to find, describe and
// highlight each thing in it.
function drawModel(width) {
  const map = state.map;
  const scene = (state.modelScene ??= modelScene());
  if (map.gl === undefined) map.gl = scene.note ? null : modelGL(map.canvas);
  const note = scene.note ?? (map.gl ? null : "The detailed view needs WebGL, which this browser has turned off.");
  map.note.textContent = note ?? "";
  map.note.hidden = !note;
  map.canvas.style.display = note ? "none" : "";
  renderModelCuts(scene);
  if (note) return { height: 0, keys: [], anchorOf: () => null, highlight() {}, pick: () => null };

  const m = map.gl;
  const scale = Math.min(width - 2 * MODEL_PAD, MODEL_SIZE) / (2 * scene.radius);
  const height = Math.ceil(2 * scene.radius * scale + 2 * MODEL_PAD);
  const ratio = window.devicePixelRatio || 1;
  map.canvas.style.height = `${height}px`;
  if (map.canvas.width !== Math.round(width * ratio) || map.canvas.height !== Math.round(height * ratio)) {
    [map.canvas.width, map.canvas.height] = [Math.round(width * ratio), Math.round(height * ratio)];
  }
  m.gl.viewport(0, 0, map.canvas.width, map.canvas.height);
  const mode = scene.levels.some(([floor]) => floor === state.modelCut) ? state.modelCut : "outside";
  // Framing the house until zoomed, then centered on what was zoomed to, or the whole lot at 1.
  const zoom = state.modelZoom ?? scene.fit;
  const camera = modelCamera(scene, width, height, scale * zoom, state.modelTarget ?? (zoom <= 1 ? scene.center : scene.houseCenter));
  // Walls are cut by which way they face, so the geometry changes only when the view turns past an axis.
  const built = mode === "outside" ? mode : `${mode} ${Math.sign(camera.view[0])} ${Math.sign(camera.view[1])}`;
  if (m.scene !== scene) renderModelList(scene);
  if (m.built !== built || m.scene !== scene) {
    const data = buildModel(scene, mode, camera.view);
    m.gl.bufferData(m.gl.ARRAY_BUFFER, data, m.gl.STATIC_DRAW);
    [m.count, m.built, m.scene, m.focusColor] = [data.length / 10, built, scene, scene.colors.focus];
  }
  const shows = modelShows(scene, mode);
  const visible = scene.items.filter((item) => shows(item) && item !== scene.lawn);
  const byKey = new Map(scene.items.map((item) => [item.key, item]));
  const layout = {
    height,
    keys: visible.map((item) => item.key),
    item: (key) => byKey.get(key),
    highlight: (key) => renderModel(m, camera, byKey.get(key)?.id ?? null, false),
    centerOf: (key) => byKey.has(key) && modelCenter(byKey.get(key), mode),
    pick: (x, y) => {
      const item = scene.items[pickModel(m, camera, x, y) - 1];
      map.pointer = item ? { key: item.key, x, y } : null; // the readout goes beside it
      return item && item !== scene.lawn ? item.key : null;
    },
    anchorOf: (key) => {
      const item = byKey.get(key);
      if (!item || !shows(item)) return null;
      const pointer = map.pointer;
      if (pointer?.key === key) return { x: pointer.x - 1, y: pointer.y - 1, width: 2, height: 2 };
      const [x, y] = camera.project(modelCenter(item, mode));
      return { x: x - 1, y: y - 1, width: 2, height: 2 };
    },
  };
  return layout;
}

// Zoom the detailed view in by a factor, out to the whole lot at 1, and
// center it on a point if given; at 1 it centers on the whole lot again.
function zoomModel(factor, target = null) {
  const fit = state.modelScene?.fit ?? 1;
  state.modelZoom = Math.min(fit * MODEL_ZOOM_MAX, Math.max(1, (state.modelZoom ?? fit) * factor));
  if (target) state.modelTarget = target;
  if (state.modelZoom === 1) state.modelTarget = null;
  state.map.pointer = null;
  drawMapSoon();
}

// The buttons that cut the house away to a level.
function renderModelCuts(scene) {
  const levels = scene.levels ?? [];
  const options = [["outside", "Outside"], ...levels.map(([floor]) => [floor, `${floor} level`])];
  const current = options.some(([id]) => id === state.modelCut) ? state.modelCut : "outside";
  const group = state.map.cuts.querySelector(".segmented");
  if (group.dataset.options === options.join() && group.dataset.current === current) return;
  [group.dataset.options, group.dataset.current] = [options.join(), current];
  group.replaceChildren(...options.map(([id, label]) => {
    const button = el("button", { type: "button", "aria-pressed": String(id === current) }, label);
    button.addEventListener("click", () => {
      state.modelCut = id;
      try {
        localStorage.setItem("house-monitor-model-cut", id);
      } catch {}
      drawMap();
    });
    return button;
  }));
}

// The list of everything placed, by kind, as a table: the view's text
// alternative, and the place to check the guesses, but in a made-up house.
function renderModelList(scene) {
  const guesses = !scene.details.example;
  state.map.list.querySelector("summary").textContent = `Everything in the detailed view${guesses ? ", known or guessed" : ""}`;
  const rows = MODEL_GROUPS.flatMap((group) => {
    const items = scene.items.filter((item) => item.group === group);
    if (!items.length) return [];
    return [
      el("tr", { class: "group" }, el("th", { colspan: guesses ? 4 : 3, scope: "colgroup" }, `${group} (${items.length})`)),
      ...items.map((item) => {
        const cells = [item.name, item.kind, item.where, ...(guesses ? [item.known ? "Known" : "Guess"] : [])];
        const row = el("tr", {}, ...cells.map((text) => el("td", {}, text)));
        row.addEventListener("pointerenter", () => setMapFocus(item.key));
        row.addEventListener("pointerleave", () => setMapFocus(null));
        return row;
      }),
    ];
  });
  const head = el("tr", {}, ...["Name", "Kind", "Where", ...(guesses ? ["Placed"] : [])].map((name) => el("th", { scope: "col" }, name)));
  state.map.listWrap.replaceChildren(el("table", {}, el("thead", {}, head), el("tbody", {}, ...rows)));
}

// The readout for a thing in the detailed view other than a room.
function modelReadout(item) {
  const rows = [el("div", { class: "head" }, el("strong", {}, item.name), " ", el("span", {}, item.kind))];
  if (item.place && !item.sensor) rows.push(el("div", { class: "time" }, `${item.place.floor} level, no sensor`));
  if (item.where && item.where !== "Outside" && item.where !== "Roof") rows.push(el("div", { class: "row" }, el("span", { class: "name" }, item.where)));
  if (!item.place && !item.known) rows.push(el("div", { class: "time" }, "Placed by guess"));
  return rows;
}

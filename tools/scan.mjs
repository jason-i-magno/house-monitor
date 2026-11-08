#!/usr/bin/env node
// Finds faces in the map's detailed view that would flicker against each other: two in one plane, overlapping,
// that differ in color or facing, where a ray from where they overlap gets out of the scene, so they can be seen.
// Checks the house whole and cut away to each level, from four ways round.
//
//   node tools/scan.mjs [house file]   # as the server picks it, if none given
import { loadHouse, loadModel, sceneOf } from "./model.mjs";

const model = loadModel();
const { buildModel } = model;
const scene = sceneOf(model, loadHouse(process.argv[2]));
if (scene.note) {
  console.log(scene.note);
  process.exit(0);
}
const modes = ["outside", ...scene.levels.map(([f]) => f)];
const views = [[1, 1], [1, -1], [-1, 1], [-1, -1]].map(([a, b]) => [a / Math.SQRT2, b / Math.SQRT2]);
const dirs = [];
for (const x of [-2, -1, 0, 1, 2]) for (const y of [-2, -1, 0, 1, 2]) for (const z of [-2, -1, 0, 1, 2]) {
  if (Math.max(Math.abs(x), Math.abs(y), Math.abs(z)) === 2 || (x && y && z)) { const l = Math.hypot(x, y, z); dirs.push([x / l + 1e-7, y / l + 2e-7, z / l + 3e-7]); }
}
const treeIds = new Set(scene.trees.map((t) => t.item.id));
const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const name = (id) => { const it = scene.items[id - 1]; return it ? `${it.name} (${it.kind})` : `#${id}`; };
const area2 = (poly) => { let s = 0; poly.forEach((p, i) => { const q = poly[(i + 1) % poly.length]; s += p[0] * q[1] - q[0] * p[1]; }); return s / 2; };
// The part of convex polygon `poly` inside convex polygon `clip` (both counterclockwise).
function clipPoly(poly, clip) {
  let out = poly;
  for (let i = 0; i < clip.length && out.length; i++) {
    const [a, b] = [clip[i], clip[(i + 1) % clip.length]];
    const side = (p) => (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]);
    const input = out; out = [];
    input.forEach((p, j) => {
      const q = input[(j + 1) % input.length];
      const [sp, sq] = [side(p), side(q)];
      if (sp >= -1e-9) out.push(p);
      if ((sp >= -1e-9) !== (sq >= -1e-9)) { const t = sp / (sp - sq); out.push([p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])]); }
    });
  }
  return out;
}
const found = new Map();
let triangleCount = 0;
for (const mode of modes) {
  for (const view of mode === "outside" ? [views[0]] : views) {
    const { data } = buildModel(scene, mode, view);
    const n = data.length / 30;
    triangleCount = Math.max(triangleCount, n);
    const tris = [];
    for (let i = 0; i < n; i++) {
      const o = i * 30;
      const p = [0, 1, 2].map((k) => [data[o + k * 10], data[o + k * 10 + 1], data[o + k * 10 + 2]]);
      let g = cross(sub(p[1], p[0]), sub(p[2], p[0]));
      const len = Math.hypot(...g);
      if (len < 1e-9) continue;
      g = g.map((c) => c / len);
      const flip = (Math.abs(g[0]) > 1e-6 ? g[0] : Math.abs(g[1]) > 1e-6 ? g[1] : g[2]) < 0 ? -1 : 1;
      g = g.map((c) => c * flip);
      const nv = [data[o + 3], data[o + 4], data[o + 5]];
      tris.push({ p, g, d: dot(g, p[0]), facing: Math.sign(dot(nv, g)), color: [data[o + 6], data[o + 7], data[o + 8]], id: data[o + 9] });
    }
    // Precomputed for ray casting (Möller–Trumbore).
    const T = new Float64Array(tris.length * 9);
    tris.forEach((t, i) => {
      const [e1, e2] = [sub(t.p[1], t.p[0]), sub(t.p[2], t.p[0])];
      T.set([...t.p[0], ...e1, ...e2], i * 9);
    });
    const CELL = 2;
    const lo = [0, 1, 2].map((k) => Math.min(...tris.map((t) => Math.min(t.p[0][k], t.p[1][k], t.p[2][k])))).map((v) => v - 1);
    const hi = [0, 1, 2].map((k) => Math.max(...tris.map((t) => Math.max(t.p[0][k], t.p[1][k], t.p[2][k])))).map((v) => v + 1);
    const dims = [0, 1, 2].map((k) => Math.ceil((hi[k] - lo[k]) / CELL));
    const grid = new Map();
    const cellOf = (v, k) => Math.min(dims[k] - 1, Math.max(0, Math.floor((v - lo[k]) / CELL)));
    tris.forEach((t, i) => {
      if (treeIds.has(t.id) && Math.min(t.p[0][2], t.p[1][2], t.p[2][2]) > scene.details.grade + 1.5) return; // you can turn the view around a tree's canopy
      const [a, b] = [0, 1, 2].map((k) => [cellOf(Math.min(t.p[0][k], t.p[1][k], t.p[2][k]), k), cellOf(Math.max(t.p[0][k], t.p[1][k], t.p[2][k]), k)]).reduce(([m, n], [c0, c1]) => [[...m, c0], [...n, c1]], [[], []]);
      for (let x = a[0]; x <= b[0]; x++) for (let y = a[1]; y <= b[1]; y++) for (let z = a[2]; z <= b[2]; z++) {
        const key = (x * dims[1] + y) * dims[2] + z;
        (grid.get(key) ?? grid.set(key, []).get(key)).push(i);
      }
    });
    let stamp = 0; const seenAt = new Int32Array(tris.length);
    const hits = (o, dir, i) => {
        const k = i * 9;
        const e1 = [T[k + 3], T[k + 4], T[k + 5]], e2 = [T[k + 6], T[k + 7], T[k + 8]];
        const h = cross(dir, e2); const a = dot(e1, h);
        if (Math.abs(a) < 1e-12) return false;
        const f = 1 / a; const s = [o[0] - T[k], o[1] - T[k + 1], o[2] - T[k + 2]];
        const u = f * dot(s, h); if (u < 0 || u > 1) return false;
        const q = cross(s, e1); const v = f * dot(dir, q); if (v < 0 || u + v > 1) return false;
        return f * dot(e2, q) > 1e-4;
    };
    // Whether a ray from o gets out of the scene's box without hitting anything (3D DDA through the grid).
    const escapes = (o, dir) => {
      stamp++;
      const c = [0, 1, 2].map((k) => cellOf(o[k], k));
      const step = dir.map((d) => (d > 0 ? 1 : -1));
      const tMax = [0, 1, 2].map((k) => { const edge = lo[k] + (c[k] + (dir[k] > 0 ? 1 : 0)) * CELL; return (edge - o[k]) / dir[k]; });
      const tDelta = dir.map((d) => CELL / Math.abs(d));
      for (;;) {
        const list = grid.get((c[0] * dims[1] + c[1]) * dims[2] + c[2]);
        if (list) for (const i of list) { if (seenAt[i] === stamp) continue; seenAt[i] = stamp; if (hits(o, dir, i)) return false; }
        const k = tMax[0] < tMax[1] ? (tMax[0] < tMax[2] ? 0 : 2) : tMax[1] < tMax[2] ? 1 : 2;
        c[k] += step[k];
        if (c[k] < 0 || c[k] >= dims[k]) return true;
        tMax[k] += tDelta[k];
      }
    };
    const buckets = new Map();
    tris.forEach((t, i) => {
      const key = `${t.g.map((c) => Math.round(c * 50)).join(",")}|${Math.round(t.d / 0.02)}`;
      (buckets.get(key) ?? buckets.set(key, []).get(key)).push(i);
    });
    for (const [key, list] of buckets) {
      const [nk, dk] = key.split("|");
      const others = [list, buckets.get(`${nk}|${Number(dk) + 1}`) ?? []];
      const t0 = tris[list[0]];
      // A basis in the plane.
      const g = t0.g;
      const u = Math.abs(g[2]) < 0.9 ? cross(g, [0, 0, 1]) : cross(g, [1, 0, 0]);
      const ul = Math.hypot(...u); const U = u.map((c) => c / ul); const V = cross(g, U);
      const flat = (t) => { let poly = t.p.map((p) => [dot(p, U), dot(p, V)]); if (area2(poly) < 0) poly = poly.reverse(); return poly; };
      for (const [which, group] of others.entries()) {
        for (let x = 0; x < list.length; x++) {
          const A = tris[list[x]];
          const pa = flat(A);
          for (let y = which === 0 ? x + 1 : 0; y < group.length; y++) {
            const B = tris[group[y]];
            if (Math.abs(A.d - B.d) > 0.004 || dot(A.g, B.g) < 1 - 1e-6) continue;
            const differ = Math.max(...A.color.map((c, k) => Math.abs(c - B.color[k]))) > 0.02 || A.facing !== B.facing;
            if (!differ) continue;
            const pb = flat(B);
            const minmax = (poly, k) => [Math.min(...poly.map((p) => p[k])), Math.max(...poly.map((p) => p[k]))];
            const [ax, bx, ay, by] = [minmax(pa, 0), minmax(pb, 0), minmax(pa, 1), minmax(pb, 1)];
            if (ax[1] <= bx[0] + 1e-6 || bx[1] <= ax[0] + 1e-6 || ay[1] <= by[0] + 1e-6 || by[1] <= ay[0] + 1e-6) continue;
            const overlap = clipPoly(pa, pb);
            if (overlap.length < 3) continue;
            const area = Math.abs(area2(overlap));
            if (area < 1e-3) continue;
            const ids = [A.id, B.id].sort((m, k) => m - k);
            const cx = [0, 1].map((k) => overlap.reduce((sum, q) => sum + q[k], 0) / overlap.length);
            const fkey = `${ids.join("-")}|${nk}|${Math.round(A.d * 10)}|${Math.round(cx[0])},${Math.round(cx[1])}`; // each spot`
            const prev = found.get(fkey);
            if (prev && prev.checked >= 3) { prev.area = Math.max(prev.area, area); continue; }
            // Sample points in the overlap; seen if a ray from just off the plane, on either side, gets out.
            const c = [overlap.reduce((s, p) => s + p[0], 0) / overlap.length, overlap.reduce((s, p) => s + p[1], 0) / overlap.length];
            const samples = [c, ...overlap.slice(0, 2).map((p) => [c[0] + 0.7 * (p[0] - c[0]), c[1] + 0.7 * (p[1] - c[1])])];
            let seen = prev?.seen ?? null;
            for (const [s0, s1] of samples) {
              if (seen) break;
              const P = [0, 1, 2].map((k) => s0 * U[k] + s1 * V[k] + A.d * g[k]);
              for (const side of [1, -1]) {
                const o = P.map((v, k) => v + side * 0.01 * g[k]);
                const out = dirs.find((dir) => side * dot(dir, g) > 0.05 && escapes(o, dir));
                if (out) { seen = { side, dir: out.map((v) => +v.toFixed(2)) }; break; }
              }
            }
            found.set(fkey, {
              a: name(ids[0]), b: name(ids[1]), mode, normal: g.map((v) => +v.toFixed(2)), d: +A.d.toFixed(3),
              at: [0, 1, 2].map((k) => +(c[0] * U[k] + c[1] * V[k] + A.d * g[k]).toFixed(2)),
              colors: [A.color, B.color].map((col) => col.map((v) => +v.toFixed(2))), facing: [A.facing, B.facing],
              area: Math.max(prev?.area ?? 0, area), checked: (prev?.checked ?? 0) + 1, seen,
            });
          }
        }
      }
    }
  }
}
const seen = [...found.values()].filter((f) => f.seen).sort((a, b) => b.area - a.area);
const differ = (f) => Math.max(...f.colors[0].map((c, k) => Math.abs(c - f.colors[1][k]))) > 0.02;
console.log(`${triangleCount} triangles at most in a view, ${found.size} pairs of faces in a plane, ${seen.length} that show`);
for (const f of seen) {
  console.log(`${f.area.toFixed(3).padStart(7)} ${f.mode.padEnd(8)} at ${f.at.join(", ")}, facing ${f.normal.join(", ")}: ${f.a} | ${f.b}${differ(f) ? "" : " (same color, facing apart)"}`);
}

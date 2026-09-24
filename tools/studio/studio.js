// unifpga board editor: draws a setup (virtual device | board | modules), traces
// any port bit, pin, wire or part end to end, and edits the setup. The server
// (tools/studio.py) evaluates every edit with the build's own code; this file
// only draws, selects and edits the setup dict.
"use strict";

const SVGNS = "http://www.w3.org/2000/svg";
const PITCH = 18;
const COLORS = ["#d9480f", "#1971c2", "#2f9e44", "#9c36b5", "#e67700", "#0c8599", "#c2255c", "#5c940d"];
const STATIC = window.STUDIO_STATIC || null;      // ./unifpga view: data inlined, read-only

const S = {
  board: null,          // /api/board/<id>
  setup: null,          // the setup being viewed / edited
  ev: null,             // /api/evaluate result {problems, configuration_text, trace}
  sel: null,            // current selection
  pending: null,        // {use, pin}: module pin waiting for a header pin
  dirty: false,
  pos: {},              // drawn positions: pins, boxes
};

// ---------------------------------------------------------------- helpers

const $ = (id) => document.getElementById(id);
function el(tag, attrs, text) {
  const e = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
  if (text !== undefined) e.textContent = text;
  return e;
}
function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k.startsWith("on")) e.addEventListener(k.slice(2), v); else e.setAttribute(k, v);
  }
  for (const k of kids) if (k !== null && k !== undefined) e.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return e;
}
function status(msg, err) { const s = $("status"); s.textContent = msg; s.className = err ? "err" : ""; }
function clone(x) { return JSON.parse(JSON.stringify(x)); }
function conn(id) { return S.board.connectors.find((c) => c.id === id); }
function moduleDef(id) { return S.board.modules.find((m) => m.id === id); }
function onboardDef(id) { return S.board.onboard.find((o) => o.id === id); }
function passive(sig) { return sig === "power" || sig === "ground"; }

// wires of a module use, plug expanded ({module pin: "conn.key"})
function wiresOf(use) {
  if (use.plug) {
    const m = moduleDef(use.module), c = conn(use.plug.connector);
    const row = c.rows[(use.plug.row || 1) - 1] || [];
    const w = {};
    for (const [p, sig] of Object.entries(m.pins)) if (!passive(sig)) w[p] = c.id + "." + row[Number(p) - 1];
    return w;
  }
  return use.wires || {};
}

// pinmap ref -> "conn.key"
function refIndex() {
  const idx = {};
  for (const c of S.board.connectors) for (const [k, p] of Object.entries(c.pins)) idx[p.ref] = c.id + "." + k;
  return idx;
}

function useLabel(use) {
  if (use.onboard) return (onboardDef(use.onboard) || {}).label || use.onboard;
  if (use.module) return (moduleDef(use.module) || {}).name || use.module;
  if (use.gpio) return "gpio " + ((conn(use.gpio) || {}).label || use.gpio);
  return "raw " + ((use.raw || {}).peripheral || "?");
}

// ---------------------------------------------------------------- trace queries

function ports() { return (S.ev && S.ev.trace && S.ev.trace.ports) || []; }

// every port of the virtual device: the traced ones, and the capability
// catalogue's other signals with no provider (drawn greyed)
function devicePorts() {
  const traced = ports();
  if (traced.length && traced[0].design_port) return traced;       // every design_top port, with widths
  const out = [];
  for (const c of (S.board && S.board.capabilities) || []) for (const s of c.signals) {
    out.push(traced.find((p) => p.capability === c.id && p.signal === s.name) ||
             {capability: c.id, signal: s.name, direction: s.direction, providers: [], none: true});
  }
  for (const p of traced) if (!out.includes(p)) out.push(p);
  return out;
}
// a port's design_top name (mic_sample, rgb_r, uart_tx, ...)
function portName(p) { return p.design_port || p.capability + "." + p.signal; }

// design bits a pinmap ref carries, with the port and the providing use
function designBitsOfRef(ref) {
  const out = [];
  for (const p of ports()) for (const pr of p.providers) for (const b of pr.bits || [])
    if (b.ref === ref && b.design_bit !== null) out.push({port: p, use: pr.attach_index, bit: b.design_bit});
  return out;
}
// the trace's record of a use's attach (pins and per-pin design-port links)
function attachOf(i) { return ((S.ev && S.ev.trace && S.ev.trace.attaches) || []).find((a) => a.attach_index === i); }
function portByKey(key) { return ports().find((p) => p.capability + "." + p.signal === key); }
function baseSignal(sig) { return String(sig).replace(/\[\d+\]$/, ""); }

// design ports one pin signal of a use serves: [{port, via, driver_port, port_at}]
function linksOfPin(i, signal) {
  const a = attachOf(i);
  return a ? ((a.links || {})[baseSignal(signal)] || []).filter((l) => portByKey(l.port)).map((l) => Object.assign({}, l, {p: portByKey(l.port)})) : [];
}

// "led[7:0]", "led[3], led[5:4]" for a sorted bit list
function bitRange(port, bits) {
  const runs = [];
  for (const b of bits) { const r = runs[runs.length - 1]; if (r && r[1] + 1 === b) r[1] = b; else runs.push([b, b]); }
  return runs.map(([a, b]) => port + (a === b ? "[" + a + "]" : "[" + b + ":" + a + "]")).join(", ");
}

// why a connection goes to several bits rather than to one
function wholePortText(ed, p) {
  const n = (ed.bits || []).length;
  return n > 1 ? "no single bit: the " + ed.via + " driver carries " + bitRange(ed.design_port, ed.bits) +
                 " over this one pin (shifted serially / time-multiplexed, or as timing such as a sync)"
               : "the pin carries " + bitRange(ed.design_port, ed.bits || []) + " through the " + ed.via + " driver";
}

// one design bit <-> pin edge, spelled out
function edgeText(ed) {
  const ri = refIndex(), hp = ri[ed.ref], c = hp && conn(hp.split(".")[0]);
  const where = hp ? c.label + " pin " + hp.split(".")[1] + " = " + ed.ref : ed.ref;
  const use = S.setup.use[ed.use];
  const p = ports().find((q) => q.design_port === ed.design_port);
  return "design " + (ed.bit === null ? bitRange(ed.design_port, ed.bits || []) + " (together, over this one pin)"
                                      : ed.design_port + "[" + ed.bit + "]") +
         (ed.via ? "  →  " + ed.via + " (driver)" : "  →  directly") +
         "  →  " + where + " = FPGA " + (ed.pin || "?") + (use ? "   [" + useLabel(use) + (ed.signal ? " " + ed.signal : "") + "]" : "");
}

// ---------------------------------------------------------------- drivers
// Logic the build puts inside the FPGA between design_top and a part's pins
// (every trace edge with a `via`). Drawn as one box per part and driver: the
// design bits it serves on its left, its pins on its right, and inside one line
// per connection, so a pin that serves several design ports shows that as the
// driver's doing (serial / multiplexed / timing) rather than as a fan-out.

function traceEdges() { return (S.ev && S.ev.trace && S.ev.trace.edges) || []; }
function pinLabel(ed) {
  const a = attachOf(ed.use), ps = (a && a.pins[ed.signal]) || [];
  const k = ps.findIndex((p) => p.ref === ed.ref);
  return ed.signal + (ps.length > 1 && k >= 0 ? "[" + k + "]" : "");
}
function designLabel(ed) { return ed.bit === null ? bitRange(ed.design_port, ed.bits || []) : ed.design_port + "[" + ed.bit + "]"; }
function designKey(ed) { return ed.design_port + ":" + (ed.bit === null ? (ed.bits || []).join(",") : ed.bit); }
function pinKey(ed) { return ed.signal + "|" + ed.ref; }

function drivers() {
  const out = new Map();
  traceEdges().forEach((ed, n) => {
    if (!ed.via) return;
    const key = ed.use + "|" + ed.via;
    if (!out.has(key)) out.set(key, {key, use: ed.use, via: ed.via, pin: new Map(), design: new Map(), edges: []});
    const d = out.get(key);
    const add = (m, k, label, extra) => { if (!m.has(k)) m.set(k, Object.assign({key: k, label, edges: []}, extra)); m.get(k).edges.push(n); };
    add(d.pin, pinKey(ed), pinLabel(ed), {ref: ed.ref});
    add(d.design, designKey(ed), designLabel(ed), {ed});
    d.edges.push(n);
  });
  return [...out.values()];
}
function driverOf(sel) { return drivers().find((d) => d.use === sel.use && d.via === sel.via); }

// a use the rig's design-wiring profile leaves out of the design: {peripheral, ties}
function profileDrop(i) { return ((S.ev && S.ev.profile_drops) || []).find((x) => x.use === i); }
function dropText(i) {
  const x = profileDrop(i);
  if (!x) return "";
  const ties = Object.entries(x.ties || {}).map(([k, v]) => k + " = " + v);
  return "left out of the design by the design-wiring profile " + (S.ev.profile || "") +
         (ties.length ? "; its pins are tied to constants: " + ties.join(", ") : "");
}

// design ports a use provides
function portsOfUse(i) {
  const out = [];
  for (const p of ports()) for (const pr of p.providers) if (pr.attach_index === i) {
    const bits = (pr.bits || []).filter((b) => b.design_bit !== null).map((b) => b.design_bit);
    out.push({port: p, bits, via: pr.via});
  }
  return out;
}

// every connection row: design bit / provider / module pin / header pin / ref / FPGA
function connectionRows() {
  const rows = [], ri = refIndex(), seen = new Set();
  const uses = S.setup.use || [];
  uses.forEach((use, i) => {
    if (!use.module) return;
    const m = moduleDef(use.module) || {pins: {}};
    for (const [p, where] of Object.entries(wiresOf(use))) {
      const [cid, key] = where.split(".");
      const c = conn(cid), pin = c && c.pins[key];
      // what the module provides; bits of other uses on the same pin (a header
      // also handed to the design as gpio) are listed after it
      const mine = pin ? ((S.ev && S.ev.trace && S.ev.trace.edges) || []).filter((ed) => ed.use === i && ed.ref === pin.ref) : [];
      const prov = mine.map((ed) => (ed.bit === null ? bitRange(ed.design_port, ed.bits || []) : ed.design_port + "[" + ed.bit + "]") +
                                    (ed.via ? " (via " + ed.via + ")" : "")).join(", ");
      const also = pin ? designBitsOfRef(pin.ref).filter((b) => b.use !== i).map((b) => portName(b.port) + "[" + b.bit + "]") : [];
      rows.push({sel: {kind: "wire", use: i, pin: p}, design: prov + (also.length ? "  (pin also " + also.join(" ") + ")" : ""),
                 provider: useLabel(use),
                 mpin: p, signal: m.pins[p], header: (c ? c.label : cid) + " pin " + key,
                 ref: pin ? pin.ref : "?", fpga: pin ? pin.pin : "?"});
      if (pin) seen.add(pin.ref);
    }
  });
  for (const p of ports()) for (const pr of p.providers) for (const b of pr.bits || []) {
    if (!b.ref || seen.has(b.ref) || b.design_bit === null) continue;
    const use = uses[pr.attach_index] || {};
    const hp = ri[b.ref];
    rows.push({sel: {kind: "vbit", cap: p.capability, signal: p.signal, bit: b.design_bit},
               design: portName(p) + "[" + b.design_bit + "]", provider: useLabel(use), mpin: "", signal: "",
               header: hp ? hp.replace(".", " pin ") : "on board", ref: b.ref, fpga: b.pin || "?"});
  }
  return rows;
}

// ---------------------------------------------------------------- drawing

function draw() {
  const svg = $("svg");
  svg.replaceChildren();
  S.pos = {pins: {}, uses: {}, ports: {}, mpins: {}, cells: {}, rows: {}, obpins: {}};
  const hi = highlight();
  const VX = 12, top = 16, OW = 290;
  let BX = 330;

  // virtual device
  let y = top + 24;
  svg.append(el("text", {x: VX, y: top + 8, "font-weight": "bold", "font-size": 14}, "Virtual device (design_top)"));
  const portRows = [];
  for (const p of devicePorts()) {
    let width = p.width || 0;
    for (const pr of p.providers) for (const b of pr.bits || []) if (b.design_bit !== null) width = Math.max(width, b.design_bit + 1);
    const perBit = p.providers.some((pr) => pr.bits);
    const none = p.none || !p.providers.length;
    const g = el("g", {class: "clickable"});
    const selPort = (S.sel && S.sel.kind === "vport" && S.sel.signal === p.signal && S.sel.cap === p.capability) ||
                    hi.vports.has(p.capability + "." + p.signal);
    g.append(el("text", {x: VX, y: y + 11, "font-size": 12, fill: selPort ? "var(--sel)" : none ? "#adb5bd" : "#212529"},
                portName(p) + (width > 1 ? "[" + (width - 1) + ":0]" : "")));
    g.append(el("text", {x: VX, y: y + 22, "font-size": 9, fill: "#868e96"}, p.capability + "." + p.signal));
    g.append(el("title", {}, "design_top " + portName(p) + (width > 1 ? "[" + (width - 1) + ":0]" : "") + " — capability " +
                             p.capability + "." + p.signal + " (" + (p.direction || "") + ")" + (none ? " — nothing in the rig provides it" : "")));
    target(g.firstChild, {kind: "vport", cap: p.capability, signal: p.signal});
    svg.append(g);
    const cells = Math.max(width, 1), per = 14;
    for (let b = 0; b < cells; b++) {
      const cx = VX + 118 + (b % per) * 13, cy = y + Math.floor(b / per) * 14;
      const on = hi.vbits.has(p.capability + "." + p.signal + "." + b) || selPort;
      const r = el("rect", {x: cx, y: cy, width: 11, height: 11, rx: 2, class: "clickable",
                           fill: on ? "var(--sel)" : none ? "#f8f9fa" : "#e7f5ff", stroke: none ? "#ced4da" : "#1c7ed6"});
      r.append(el("title", {}, portName(p) + (width > 1 ? "[" + b + "]" : "") + "  (" + p.capability + "." + p.signal + ")"));
      S.pos.cells[portName(p) + "." + b] = {x: cx + 11, y: cy + 5.5};
      target(r, width > 1 || perBit ? {kind: "vbit", cap: p.capability, signal: p.signal, bit: b} : {kind: "vport", cap: p.capability, signal: p.signal});
      svg.append(r);
    }
    const rowsUsed = Math.max(Math.ceil(cells / per), 2) - 0.6;          // room for the capability name
    portRows.push({p, x: VX + 118 + Math.min(cells, per) * 13, y: y + 6});
    S.pos.rows[portName(p)] = {x: VX + 118 + Math.min(cells, per) * 13 + 6, y: y + 6};
    y += 14 * rowsUsed + 8;
  }
  if (!ports().length) svg.append(el("text", {x: VX, y: y + 10, fill: "#868e96"}, "(nothing in the rig can be traced yet)"));

  // connections that carry several bits of a port over one pin (through a
  // driver): an outline around exactly those bits, the line starts at it
  S.pos.groups = {};
  const allEdges = (S.ev && S.ev.trace && S.ev.trace.edges) || [];
  allEdges.forEach((ed, n) => {
    if (ed.bit !== null) return;
    const key = ed.design_port + ":" + ed.bits.join(",");
    if (!S.pos.groups[key]) {
      const cellsOf = ed.bits.map((b) => [b, S.pos.cells[ed.design_port + "." + b]]).filter(([, c]) => c);
      if (!cellsOf.length) return;
      const runs = [];
      for (const [b, c] of cellsOf) {
        const last = runs[runs.length - 1];
        if (last && last.b + 1 === b && last.y === c.y) { last.x1 = c.x; last.b = b; }
        else runs.push({x0: c.x - 11, x1: c.x, y: c.y, b});
      }
      const end = runs[runs.length - 1];
      S.pos.groups[key] = {runs, x: end.x1 + 4, y: end.y, edges: []};
    }
    S.pos.groups[key].edges.push(n);
  });
  for (const g of Object.values(S.pos.groups)) {
    const lit = g.edges.some((n) => hi.edges.has(n));
    for (const r of g.runs)
      svg.append(el("rect", {x: r.x0 - 2, y: r.y - 8, width: r.x1 - r.x0 + 4, height: 16, rx: 3, fill: "none",
                             stroke: lit ? "var(--sel)" : "#4c6ef5", "stroke-width": lit ? 2 : 1, "stroke-dasharray": lit ? "" : "3 2"}));
  }

  // drivers: a column between the virtual device and the board, each box level
  // with the design bits it serves, anchors in the order of those bits
  const DRV = drivers(), DX = VX + 118 + 14 * 13 + 80, DW = 200, ROW = 13, HEAD = 32;
  const srcOf = (ed) => ed.bit === null ? (S.pos.groups[ed.design_port + ":" + (ed.bits || []).join(",")] || S.pos.rows[ed.design_port])
                                        : S.pos.cells[ed.design_port + "." + ed.bit];
  const srcY = (an) => (srcOf(an.ed) || {y: 0}).y;
  let dy = top + 24;
  for (const d of DRV) { const ys = [...d.design.values()].map(srcY); d.want = ys.reduce((s, v) => s + v, 0) / Math.max(ys.length, 1); }
  for (const d of DRV.sort((a, b) => a.want - b.want)) {
    const rows = Math.max(d.pin.size, d.design.size);
    d.h = HEAD + rows * ROW;
    d.y = Math.max(dy, d.want - d.h / 2);
    dy = d.y + d.h + 16;
    const place = (list, x) => list.forEach((an, k) => { an.x = x; an.y = d.y + HEAD + ((rows - list.length) / 2 + k) * ROW + ROW / 2; });
    place([...d.design.values()].sort((a, b) => srcY(a) - srcY(b)), DX);
    place([...d.pin.values()], DX + DW);
  }
  if (DRV.length) BX = DX + DW + 110;

  // board frame, on-board devices
  const used = new Map();
  (S.setup.use || []).forEach((u, i) => { if (u.onboard) used.set(u.onboard, i); });
  let oy = top + 40;
  const boardItems = [];
  for (const o of S.board.onboard) {
    const i = used.has(o.id) ? used.get(o.id) : null;
    const dropped = i !== null && !!profileDrop(i);
    const on = i !== null && hi.uses.has(i), selected = S.sel && S.sel.kind === "onboard" && S.sel.id === o.id;
    const g = el("g", {class: "clickable"});
    const obpins = i === null ? [] : Object.entries(o.pins).flatMap(([s, ps]) => ps.map((pp) => Object.assign({s}, pp)));
    const perRow = Math.floor((OW - 20) / 11), pinRows = Math.ceil(obpins.length / perRow);
    const boxH = 20 + pinRows * 11;
    g.append(el("rect", {x: BX + 12, y: oy, width: OW, height: boxH, rx: 3,
                        fill: dropped ? "#f1f3f5" : i !== null ? "#d3f9d8" : "#ffffff", stroke: on || selected ? "var(--sel)" : "#868e96",
                        "stroke-width": on || selected ? 2.5 : 1, "stroke-dasharray": dropped && !(on || selected) ? "4 2" : ""}));
    obpins.forEach((pp, k) => {
      const px = BX + 22 + (k % perRow) * 11, py = oy + 24 + Math.floor(k / perRow) * 11;
      S.pos.obpins[pp.ref] = {x: px, y: py};
      const lit = hi.refs.has(pp.ref);
      const dot = el("circle", {cx: px, cy: py, r: 4, fill: lit ? "var(--sel)" : "#ffffff", stroke: lit ? "var(--sel)" : dropped ? "#adb5bd" : "#2b8a3e"});
      dot.append(el("title", {}, o.label + ": " + pp.s + " = " + pp.ref + " = FPGA " + (pp.pin || "?") + (dropped ? " — not connected to the design (profile tie)" : "")));
      target(dot, {kind: "ref", ref: pp.ref});
      g.append(dot);
    });
    const lt = el("text", {x: BX + 18, y: oy + 14, "font-size": 12, fill: i === null || dropped ? "#868e96" : "#212529"},
                  o.label + (dropped ? "  — not in the design (profile)" : ""));
    lt.append(el("title", {}, o.label + (i === null ? " — not used by this setup" : dropped ? " — " + dropText(i) : "")));
    g.append(lt);
    target(g.firstChild, {kind: "onboard", id: o.id});
    target(lt, {kind: "onboard", id: o.id});
    boardItems.push(g);
    if (i !== null) S.pos.uses[i] = {x: BX + 12, y: oy + 10, xr: BX + 12 + OW};
    oy += boxH + 6;
  }

  // connectors
  const GX = BX + OW + 50;
  let cy = top + 50, widest = 0;
  const connItems = [];
  const gpioConn = new Map();
  (S.setup.use || []).forEach((u, i) => { if (u.gpio) gpioConn.set(u.gpio, i); });
  for (const c of S.board.connectors) {
    const cols = Math.max(...c.rows.map((r) => r.length));
    const long = c.rows.flat().some((k) => k.length > 2);
    const lab = long ? 26 : 12;               // room for pin names
    const w = cols * PITCH;
    widest = Math.max(widest, w);
    const gi = gpioConn.has(c.id) ? gpioConn.get(c.id) : null;
    const csel = (S.sel && S.sel.kind === "conn" && S.sel.id === c.id) || (gi !== null && hi.uses.has(gi));
    const g = el("g");
    const lbl = el("text", {x: GX - 6, y: cy - 8, "font-size": 12, class: "clickable", fill: csel ? "var(--sel)" : "#343a40"},
                   c.label + (gi !== null ? "  — design gpio" : ""));
    target(lbl, {kind: "conn", id: c.id});
    g.append(lbl);
    const boxY = cy + lab + 6;
    g.append(el("rect", {x: GX - 6, y: boxY - 6, width: w + 12, height: c.rows.length * PITCH + 12, rx: 3,
                        fill: "#f8f9fa", stroke: gi !== null ? "#2b8a3e" : "#495057", "stroke-width": gi !== null ? 2.5 : 1}));
    c.rows.forEach((row, r) => row.forEach((key, k) => {
      const px = GX + k * PITCH + PITCH / 2, py = boxY + r * PITCH + PITCH / 2;
      S.pos.pins[c.id + "." + key] = {x: px, y: py};
      const pin = c.pins[key], pw = c.power[key];
      const isSel = hi.pins.has(c.id + "." + key);
      const fill = pin ? (isSel ? "var(--sel)" : "#ffffff") : pw === "VCC" ? "#ffc9c9" : pw === "GND" ? "#ced4da" : "#e9ecef";
      const circ = el("circle", {cx: px, cy: py, r: 6, fill, stroke: isSel ? "var(--sel)" : "#495057",
                                 "stroke-width": isSel ? 2 : 1, class: pin ? "clickable" : ""});
      circ.append(el("title", {}, c.label + " pin " + key + (pin ? ": " + pin.ref + " = " + pin.pin : pw ? ": " + pw : "")));
      if (pin) target(circ, {kind: "pin", conn: c.id, key});
      g.append(circ);
      // the pin's number / name outside its row
      const above = r === 0;
      if (long) {
        const t = el("text", {x: px + 3, y: above ? boxY - 8 : boxY + c.rows.length * PITCH + 8, "font-size": 8,
                              transform: "rotate(-90 " + (px + 3) + " " + (above ? boxY - 8 : boxY + c.rows.length * PITCH + 8) + ")",
                              "text-anchor": above ? "start" : "end", fill: "#495057"}, key);
        g.append(t);
      } else {
        g.append(el("text", {x: px, y: above ? boxY - 8 : boxY + c.rows.length * PITCH + 14, "font-size": 9,
                             "text-anchor": "middle", fill: "#495057"}, key));
      }
    }));
    if (gi !== null) S.pos.uses[gi] = {x: GX - 6, y: boxY + 4, xr: GX + w + 6};
    connItems.push(g);
    cy = boxY + c.rows.length * PITCH + (long ? 50 : 40);
  }
  const boardW = GX - BX + widest + 24, boardH = Math.max(cy - top, oy - top + 10);
  svg.append(el("rect", {x: BX, y: top, width: boardW, height: boardH, rx: 8, fill: "#e7f5ff", stroke: "#1c7ed6", "stroke-width": 2}));
  svg.append(el("text", {x: BX + 12, y: top + 24, "font-weight": "bold", "font-size": 15}, S.board.board));
  boardItems.forEach((g) => svg.append(g));
  connItems.forEach((g) => svg.append(g));

  // modules and raw uses
  const MX = BX + boardW + 170;
  let my = top;
  const wires = [];
  let colour = 0;
  (S.setup.use || []).forEach((use, i) => {
    if (!use.module && !use.raw) return;
    const col = COLORS[colour++ % COLORS.length];
    const m = use.module ? moduleDef(use.module) : null;
    const w = use.module ? wiresOf(use) : {};
    const pins = m ? Object.keys(m.pins).filter((p) => !passive(m.pins[p])) : [];
    const hgt = 28 + PITCH * Math.max(pins.length, 1);
    const on = hi.uses.has(i) || (S.sel && S.sel.kind === "use" && S.sel.use === i);
    const g = el("g");
    const box = el("rect", {x: MX, y: my, width: 230, height: hgt, rx: 5, fill: "#fff", stroke: col,
                           "stroke-width": on ? 3.5 : 2, class: "clickable"});
    target(box, {kind: "use", use: i});
    g.append(box);
    const title = el("text", {x: MX + 8, y: my + 17, "font-weight": "bold", "font-size": 12, fill: col, class: "clickable"}, useLabel(use));
    target(title, {kind: "use", use: i});
    g.append(title);
    S.pos.uses[i] = {x: MX, y: my + 12, xr: MX + 230};
    pins.forEach((p, k) => {
      const py = my + 28 + k * PITCH + PITCH / 2;
      S.pos.mpins[i + "." + p] = {x: MX, y: py};
      const pend = S.pending && S.pending.use === i && S.pending.pin === p;
      const isSel = hi.mpins.has(i + "." + p);
      const t = el("text", {x: MX + 16, y: py + 4, "font-size": 11, class: "clickable",
                            fill: pend ? "#f76707" : isSel ? "var(--sel)" : "#343a40",
                            "font-weight": pend || isSel ? "bold" : "normal"},
                   p + "  (" + m.pins[p] + ")" + (w[p] ? "  → " + w[p] : "  — not wired"));
      target(t, {kind: "mpin", use: i, pin: p});
      g.append(t);
      const dot = el("circle", {cx: MX, cy: py, r: 4, fill: col});
      g.append(dot);
      if (w[p]) wires.push({use: i, pin: p, from: {x: MX, y: py}, to: w[p], col});
    });
    if (!m) g.append(el("text", {x: MX + 8, y: my + 36, "font-size": 11, fill: "#868e96"}, "not modelled physically"));
    svg.append(g);
    my += hgt + 18;
  });

  // wires
  for (const wdef of wires) {
    const to = S.pos.pins[wdef.to];
    if (!to) continue;
    const isSel = hi.wires.has(wdef.use + "." + wdef.pin);
    const d = "M" + wdef.from.x + "," + wdef.from.y + " C" + (wdef.from.x - 100) + "," + wdef.from.y + " " +
              (to.x + 80) + "," + to.y + " " + (to.x + 7) + "," + to.y;
    const hit = el("path", {d, fill: "none", stroke: "transparent", "stroke-width": 10, class: "clickable"});
    target(hit, {kind: "wire", use: wdef.use, pin: wdef.pin});
    const path = el("path", {d, fill: "none", stroke: isSel ? "var(--sel)" : wdef.col, "stroke-width": isSel ? 3.5 : 1.6,
                             opacity: S.sel && !isSel ? 0.35 : 0.9});
    path.append(el("title", {}, useLabel(S.setup.use[wdef.use]) + " " + wdef.pin + " → " + wdef.to));
    svg.append(path);
    svg.append(hit);
  }

  // design bit -> pin edges (tools/trace.py edges): to an on-board device's pin
  // dot or a header pin; faint, the selection's in red
  const ri = refIndex();
  const pinAt = (ref) => ri[ref] ? S.pos.pins[ri[ref]] : S.pos.obpins[ref];
  const line = (d, lit, dash, sel, tip) => {
    svg.append(el("path", {d, fill: "none", stroke: lit ? "var(--sel)" : dash ? "#4c6ef5" : "#2f9e44",
                           "stroke-width": lit ? 2.4 : 1.2, opacity: lit ? 1 : S.sel ? 0.18 : 0.55, "stroke-dasharray": dash ? "6 3" : ""}));
    const hit = el("path", {d, fill: "none", stroke: "transparent", "stroke-width": 9, "stroke-linecap": "round", class: "clickable"});
    hit.append(el("title", {}, tip));
    target(hit, sel);
    svg.append(hit);
  };
  const curve = (a, b, bend) => "M" + a.x + "," + a.y + " C" + (a.x + bend) + "," + a.y + " " + (b.x - bend) + "," + b.y + " " + b.x + "," + b.y;
  traceEdges().forEach((ed, n) => {
    if (ed.via) return;                       // drawn through its driver's box below
    const from = srcOf(ed), to = pinAt(ed.ref);
    if (!from || !to) return;
    line(curve(from, {x: to.x - 6, y: to.y}, 90), hi.edges.has(n), false, {kind: "edge", n}, edgeText(ed));
  });
  const all = traceEdges();
  for (const d of DRV) {
    const use = S.setup.use[d.use] || {}, lit = (list) => list.some((n) => hi.edges.has(n));
    const boxSel = S.sel && S.sel.kind === "driver" && S.sel.use === d.use && S.sel.via === d.via;
    const g = el("g", {class: "clickable"});
    g.append(el("rect", {x: DX, y: d.y, width: DW, height: d.h, rx: 5, fill: "#f3f0ff",
                         stroke: boxSel || lit(d.edges) ? "var(--sel)" : "#7048e8", "stroke-width": boxSel ? 2.5 : 1.2}));
    g.append(el("text", {x: DX + 8, y: d.y + 14, "font-size": 11, "font-weight": "bold", fill: "#5f3dc4"}, d.via));
    g.append(el("text", {x: DX + 8, y: d.y + 26, "font-size": 9, fill: "#868e96"}, "driver in the FPGA for " + useLabel(use)));
    g.append(el("title", {}, d.via + ": logic inside the FPGA between design_top and " + useLabel(use) + "'s pins"));
    for (const c of g.children) if (c.tagName !== "title") target(c, {kind: "driver", use: d.use, via: d.via});
    svg.append(g);
    for (const [m, x, anchor, side] of [[d.design, DX + 5, "start", "design"], [d.pin, DX + DW - 5, "end", "pin"]])
      for (const an of m.values()) {
        const on = lit(an.edges);
        svg.append(el("text", {x, y: an.y + 3, "font-size": 9, "text-anchor": anchor, fill: on ? "var(--sel)" : "#495057"}, an.label));
        svg.append(el("circle", {cx: side === "design" ? DX : DX + DW, cy: an.y, r: 2.5, fill: on ? "var(--sel)" : "#7048e8"}));
        if (side === "design") {
          const from = srcOf(an.ed);
          if (from) line(curve(from, {x: DX - 3, y: an.y}, 50), on, true, {kind: "dseg", use: d.use, via: d.via, side, key: an.key},
                         "design " + an.label + " ↔ " + d.via + " driver (click for what it carries)");
        } else {
          const to = pinAt(an.ref);
          if (to) line(curve({x: DX + DW + 3, y: an.y}, {x: to.x - 6, y: to.y}, 70), on, true, {kind: "dseg", use: d.use, via: d.via, side, key: an.key},
                       d.via + " pin " + an.label + " ↔ " + an.ref + " (click for what it carries)");
        }
      }
    // inside: one line per connection, left anchor (design bits) to right anchor (pin)
    for (const n of d.edges) {
      const ed = all[n], l = d.design.get(designKey(ed)), r = d.pin.get(pinKey(ed));
      line(curve({x: DX + 96, y: l.y}, {x: DX + DW - 50, y: r.y}, 22), hi.edges.has(n), true, {kind: "edge", n}, edgeText(ed));
    }
  }

  // legend
  const LY = Math.max(top + boardH, my, y, dy) + 14;
  const vias = [...new Set(DRV.map((d) => d.via))];
  const legend = [["#2f9e44", "", "design bit wired straight to a pin"]];
  if (vias.length) legend.push(["#4c6ef5", "6 3", "through a driver inside the FPGA (the violet boxes: " + vias.join(", ") +
                                "): design bits → driver → pins; the lines inside show which pin serves which bits"]);
  if (Object.keys(S.pos.groups).length) legend.push(["#4c6ef5", "3 2", "dotted outline: design bits a driver carries together over one pin"]);
  if (wires.length) legend.push(["#d9480f", "", "wire from a header pin to a module (one colour per module)"]);
  if (((S.ev && S.ev.profile_drops) || []).length) legend.push(["#adb5bd", "4 2", "part in the rig the design-wiring profile leaves out of the design (pins tied to constants)"]);
  legend.push(["var(--sel)", "", "the selection and everything it connects to (dashed when through a driver)"]);
  legend.forEach(([col, dash, text], k) => {
    const ly = LY + k * 16;
    svg.append(el("path", {d: "M" + VX + "," + ly + " l40,0", stroke: col, "stroke-width": 2, "stroke-dasharray": dash}));
    svg.append(el("text", {x: VX + 48, y: ly + 4, "font-size": 11, fill: "#495057"}, text));
  });
  svg.append(el("text", {x: VX, y: LY + legend.length * 16 + 4, "font-size": 11, fill: "#868e96"},
                "Click any line, pin, bit or part: the panel on the right says what the connection is."));

  const W = MX + 260, H = LY + (legend.length + 1) * 16 + 14;
  S.content = {w: W, h: H};
  applyView();
}

// ---------------------------------------------------------------- what a click means
// Every clickable shape says what selecting it means (data-sel). A click looks
// at everything under the pointer: pins and bits ("points"), connections
// (design <-> pin lines, header <-> module wires) and parts (devices, modules,
// connectors). The platform's command key (⌘ on macOS, Ctrl elsewhere) with a
// click prefers points, its option key (⌥ / Alt) connections; a plain click on
// overlapping candidates asks which one, naming the keys as the platform does.

const MAC = /mac|iphone|ipad/i.test((navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || navigator.userAgent);
const MODS = MAC ? {points: "⌘", pointsKey: "metaKey", conns: "⌥", connsKey: "altKey"}
                 : {points: "Ctrl", pointsKey: "ctrlKey", conns: "Alt", connsKey: "altKey"};

const POINTS = new Set(["vbit", "vport", "pin", "ref", "mpin"]);
const CONNECTIONS = new Set(["edge", "dseg", "wire"]);

function target(elem, sel) {
  elem.setAttribute("data-sel", JSON.stringify(sel));
  elem.classList.add("clickable");
}

function selLabel(sel) {
  const ev = (S.ev && S.ev.trace) || {};
  if (sel.kind === "edge") return "connection: " + edgeText((ev.edges || [])[sel.n] || {});
  if (sel.kind === "dseg" || sel.kind === "driver") {
    const d = driverOf(sel), an = d && sel.kind === "dseg" && (sel.side === "pin" ? d.pin : d.design).get(sel.key);
    if (sel.kind === "driver" || !an) return "driver: " + sel.via;
    return sel.side === "pin" ? "connection: " + sel.via + " pin " + an.label + " ↔ " + an.ref : "connection: design " + an.label + " ↔ " + sel.via + " driver";
  }
  if (sel.kind === "wire") { const u = S.setup.use[sel.use]; return "wire: " + useLabel(u) + " pin " + sel.pin + " → header " + wiresOf(u)[sel.pin]; }
  if (sel.kind === "mpin") return "module pin: " + useLabel(S.setup.use[sel.use]) + " " + sel.pin;
  if (sel.kind === "pin") { const c = conn(sel.conn), p = c && c.pins[sel.key]; return "header pin: " + c.label + " pin " + sel.key + (p ? " (" + p.ref + " = " + p.pin + ")" : ""); }
  if (sel.kind === "ref") return "on-board pin: " + sel.ref;
  if (sel.kind === "vbit") { const p = ports().find((q) => q.capability === sel.cap && q.signal === sel.signal); return "design bit: " + (p ? portName(p) : sel.signal) + "[" + sel.bit + "]"; }
  if (sel.kind === "vport") { const p = ports().find((q) => q.capability === sel.cap && q.signal === sel.signal); return "design port: " + (p ? portName(p) : sel.signal); }
  if (sel.kind === "use") return "part: " + useLabel(S.setup.use[sel.use]);
  if (sel.kind === "onboard") return "on-board device: " + ((onboardDef(sel.id) || {}).label || sel.id);
  if (sel.kind === "conn") return "connector: " + ((conn(sel.id) || {}).label || sel.id);
  return sel.kind;
}

function candidatesAt(x, y) {
  const seen = new Set(), out = [];
  for (const e of document.elementsFromPoint(x, y)) {
    const t = e.closest && e.closest("[data-sel]");
    if (!t) continue;
    const key = t.getAttribute("data-sel");
    if (!seen.has(key)) { seen.add(key); out.push(JSON.parse(key)); }
  }
  return out;
}

function activate(sel) {
  closePicker();
  if (sel.kind === "pin") return pinClicked(sel.conn, sel.key);
  if (sel.kind === "mpin") return modulePinClicked(sel.use, sel.pin);
  select(sel);
}

function onSvgClick(e) {
  const all = candidatesAt(e.clientX, e.clientY);
  const points = all.filter((s) => POINTS.has(s.kind)), conns = all.filter((s) => CONNECTIONS.has(s.kind));
  const parts = all.filter((s) => !POINTS.has(s.kind) && !CONNECTIONS.has(s.kind));
  let pick = null;
  if (e.metaKey || e.ctrlKey) pick = points[0] || parts[0] || conns[0];
  else if (e.altKey) pick = conns[0] || points[0] || parts[0];
  else {
    const primary = points.concat(conns);
    if (primary.length > 1) return openPicker(e.clientX, e.clientY, primary);
    pick = primary[0] || parts[0];
  }
  if (pick) activate(pick);
}

function closePicker() { const p = $("picker"); if (p) p.remove(); }

function openPicker(x, y, options) {
  closePicker();
  const box = h("div", {id: "picker"},
    h("div", {class: "picker-head"}, "Several things are under the pointer — pick one:"),
    ...options.slice(0, 12).map((s) => h("button", {class: "picker-item", onclick: () => activate(s)}, selLabel(s))),
    h("div", {class: "picker-tip"}, MODS.points + "-click picks the pin or bit, " + MODS.conns + "-click the connection."));
  box.style.left = Math.min(x + 8, window.innerWidth - 420) + "px";
  box.style.top = Math.min(y + 8, window.innerHeight - 40 - 28 * Math.min(options.length, 12)) + "px";
  document.body.append(box);
}

// ---------------------------------------------------------------- zoom and pan
// S.view is the visible part of the drawing (SVG user units); null = fit it all.
// Wheel zooms around the cursor, dragging pans; a drag does not click.

function applyView() {
  const svg = $("svg"), c = S.content || {w: 100, h: 100};
  const v = S.view || {x: 0, y: 0, w: c.w, h: c.h};
  svg.setAttribute("viewBox", [v.x, v.y, v.w, v.h].join(" "));
  const z = $("zoom-level");
  if (z) z.textContent = Math.round(100 * (S.view ? c.w / v.w : 1)) + "%";
}

function currentView() {
  const c = S.content;
  if (S.view) return S.view;
  // the fitted view with the drawing box's aspect, as the browser shows it
  const r = $("svg").getBoundingClientRect(), k = Math.max(c.w / r.width, c.h / r.height);
  return {x: 0, y: 0, w: r.width * k, h: r.height * k};
}

function svgPoint(evt) {
  const r = $("svg").getBoundingClientRect(), v = currentView();
  return {x: v.x + (evt.clientX - r.left) / r.width * v.w, y: v.y + (evt.clientY - r.top) / r.height * v.h};
}

function zoomAt(p, factor) {
  const v = currentView(), c = S.content;
  const w = Math.min(Math.max(v.w * factor, c.w / 20), c.w * 4);
  const k = w / v.w;
  S.view = {x: p.x - (p.x - v.x) * k, y: p.y - (p.y - v.y) * k, w, h: v.h * k};
  applyView();
}

function zoomCenter(factor) {
  const v = currentView();
  zoomAt({x: v.x + v.w / 2, y: v.y + v.h / 2}, factor);
}

function wireZoomPan() {
  const svg = $("svg");
  svg.addEventListener("wheel", (e) => {
    e.preventDefault();
    const f = Math.pow(1.0015, e.deltaMode === 1 ? e.deltaY * 30 : e.deltaY);   // lines or pixels
    zoomAt(svgPoint(e), f);
  }, {passive: false});
  let drag = null;
  svg.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    drag = {x: e.clientX, y: e.clientY, view: currentView(), moved: false, id: e.pointerId};
  });
  svg.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (!drag.moved && Math.hypot(dx, dy) < 4) return;
    if (!drag.moved) { drag.moved = true; svg.setPointerCapture(drag.id); svg.classList.add("panning"); }
    const r = svg.getBoundingClientRect(), v = drag.view;
    S.view = {x: v.x - dx / r.width * v.w, y: v.y - dy / r.height * v.h, w: v.w, h: v.h};
    applyView();
  });
  const end = () => {
    if (drag && drag.moved) {
      svg.classList.remove("panning");
      // the click that ends a drag is not a selection
      svg.addEventListener("click", (ev) => { ev.stopPropagation(); ev.preventDefault(); }, {capture: true, once: true});
    }
    drag = null;
  };
  svg.addEventListener("pointerup", end);
  svg.addEventListener("pointercancel", end);
  svg.addEventListener("click", onSvgClick);
  document.addEventListener("pointerdown", (e) => { const p = $("picker"); if (p && !p.contains(e.target)) closePicker(); });
  $("zoom-in").addEventListener("click", () => zoomCenter(1 / 1.25));
  $("zoom-out").addEventListener("click", () => zoomCenter(1.25));
  $("zoom-fit").addEventListener("click", () => { S.view = null; applyView(); });
  svg.addEventListener("dblclick", (e) => { if (e.target === svg) { S.view = null; applyView(); } });
}

// what the current selection lights up
function highlight() {
  const hi = {pins: new Set(), wires: new Set(), mpins: new Set(), uses: new Set(), vbits: new Set(), links: new Set(),
              vports: new Set(), edges: new Set(), refs: new Set()};
  const sel = S.sel;
  if (!sel) return hi;
  const allEdges = (S.ev && S.ev.trace && S.ev.trace.edges) || [];
  const ri0 = refIndex();
  // light an edge and both of its ends
  const takeEdge = (ed, n) => {
    hi.edges.add(n); hi.refs.add(ed.ref);
    if (ri0[ed.ref]) hi.pins.add(ri0[ed.ref]);
    const p = ports().find((q) => q.design_port === ed.design_port);
    if (!p) return;
    for (const b of ed.bits || (ed.bit === null ? [] : [ed.bit])) hi.vbits.add(p.capability + "." + p.signal + "." + b);
  };
  const edgesWhere = (f) => allEdges.forEach((ed, n) => { if (f(ed)) takeEdge(ed, n); });
  const ri = refIndex();
  // the module pins of use i that serve design port p: the pins its edges reach
  const addServingPins = (i, p) => {
    hi.uses.add(i);
    const use = S.setup.use[i];
    const refs = new Set(allEdges.filter((ed) => ed.use === i && ed.design_port === p.design_port).map((ed) => ed.ref));
    for (const r of refs) { hi.refs.add(r); if (ri0[r]) hi.pins.add(ri0[r]); }
    if (!use || !use.module) return;
    for (const [mp, w] of Object.entries(wiresOf(use)))
      if ([...refs].some((r) => ri0[r] === w)) { hi.wires.add(i + "." + mp); hi.mpins.add(i + "." + mp); }
  };
  const addUse = (i) => {
    hi.uses.add(i);
    const use = S.setup.use[i];
    if (use && use.module) for (const [p, w] of Object.entries(wiresOf(use))) { hi.wires.add(i + "." + p); hi.mpins.add(i + "." + p); hi.pins.add(w); }
  };
  const addBit = (p, bit) => {
    hi.vbits.add(p.capability + "." + p.signal + "." + bit);
    for (const pr of p.providers) for (const b of pr.bits || []) if (b.design_bit === bit) {
      hi.links.add(p.capability + "." + p.signal + "#" + pr.attach_index);
      hi.uses.add(pr.attach_index);
      if (b.ref && ri[b.ref]) {
        hi.pins.add(ri[b.ref]);
        const use = S.setup.use[pr.attach_index];
        if (use && use.module) for (const [mp, w] of Object.entries(wiresOf(use))) if (w === ri[b.ref]) { hi.wires.add(pr.attach_index + "." + mp); hi.mpins.add(pr.attach_index + "." + mp); }
      }
      if (!b.ref) addServingPins(pr.attach_index, p);      // through a driver: the pins serving this port
    }
  };
  // an edge, its ends and the module wire that carries it
  const takeEdgeWire = (ed, n) => {
    takeEdge(ed, n);
    hi.uses.add(ed.use);
    const hp = ri0[ed.ref], use = S.setup.use[ed.use];
    if (hp && use && use.module) for (const [mp, w] of Object.entries(wiresOf(use))) if (w === hp) { hi.wires.add(ed.use + "." + mp); hi.mpins.add(ed.use + "." + mp); }
  };
  if (sel.kind === "edge") {
    const ed = allEdges[sel.n];
    if (ed) takeEdgeWire(ed, sel.n);
  } else if (sel.kind === "dseg" || sel.kind === "driver") {
    const d = driverOf(sel);
    const an = d && sel.kind === "dseg" ? (sel.side === "pin" ? d.pin : d.design).get(sel.key) : null;
    for (const n of (sel.kind === "driver" ? (d ? d.edges : []) : (an ? an.edges : []))) takeEdgeWire(allEdges[n], n);
    hi.uses.add(sel.use);
  } else if (sel.kind === "ref") {
    edgesWhere((ed) => ed.ref === sel.ref);
    hi.refs.add(sel.ref);
  } else if (sel.kind === "vbit") {
    const p = ports().find((q) => q.capability === sel.cap && q.signal === sel.signal);
    if (p) { addBit(p, sel.bit); edgesWhere((ed) => ed.design_port === p.design_port && (ed.bits || [ed.bit]).includes(sel.bit)); }
  } else if (sel.kind === "vport") {
    const p = ports().find((q) => q.capability === sel.cap && q.signal === sel.signal);
    if (p) edgesWhere((ed) => ed.design_port === p.design_port);
    if (p) for (const pr of p.providers) {
      hi.links.add(p.capability + "." + p.signal + "#" + pr.attach_index);
      addServingPins(pr.attach_index, p);
      for (const b of pr.bits || []) { if (b.design_bit !== null) hi.vbits.add(p.capability + "." + p.signal + "." + b.design_bit); if (b.ref && ri[b.ref]) hi.pins.add(ri[b.ref]); }
    }
  } else if (sel.kind === "use" || sel.kind === "onboard" || sel.kind === "conn") {
    let i = sel.use;
    if (sel.kind === "onboard") i = (S.setup.use || []).findIndex((u) => u.onboard === sel.id);
    if (sel.kind === "conn") i = (S.setup.use || []).findIndex((u) => u.gpio === sel.id);
    if (i !== undefined && i >= 0) {
      addUse(i);
      edgesWhere((ed) => ed.use === i);
      for (const x of portsOfUse(i)) {
        hi.links.add(x.port.capability + "." + x.port.signal + "#" + i);
        for (const b of x.bits) hi.vbits.add(x.port.capability + "." + x.port.signal + "." + b);
      }
    }
  } else if (sel.kind === "pin" || sel.kind === "wire") {
    let key = sel.kind === "pin" ? sel.conn + "." + sel.key : wiresOf(S.setup.use[sel.use])[sel.pin];
    hi.pins.add(key);
    const kc = conn(key.split(".")[0]), kp = kc && kc.pins[key.split(".")[1]];
    if (kp) edgesWhere((ed) => ed.ref === kp.ref);
    (S.setup.use || []).forEach((u, i) => { if (u.module) for (const [p, w] of Object.entries(wiresOf(u))) if (w === key) {
      hi.wires.add(i + "." + p); hi.mpins.add(i + "." + p); hi.uses.add(i);
    } });
    const [cid, k] = key.split(".");
    const pin = conn(cid) && conn(cid).pins[k];
    if (pin) for (const b of designBitsOfRef(pin.ref)) { hi.vbits.add(b.port.capability + "." + b.port.signal + "." + b.bit); hi.links.add(b.port.capability + "." + b.port.signal + "#" + b.use); }
  }
  return hi;
}

// ---------------------------------------------------------------- selection and details

function select(sel) { S.sel = sel; S.pending = null; render(); }

function pinClicked(cid, key) {
  if (S.pending && !STATIC) {
    const use = S.setup.use[S.pending.use];
    if (use.plug) { use.wires = wiresOf(use); delete use.plug; }
    use.wires = use.wires || {};
    for (const [p, w] of Object.entries(use.wires)) if (w === cid + "." + key && p !== S.pending.pin) delete use.wires[p];
    use.wires[S.pending.pin] = cid + "." + key;
    const pin = S.pending.pin;
    S.pending = null;
    changed("wired " + pin + " to " + cid + "." + key);
    S.sel = {kind: "wire", use: S.setup.use.indexOf(use), pin};
    return;
  }
  select({kind: "pin", conn: cid, key});
}

function modulePinClicked(i, p) {
  if (STATIC) return select({kind: "wire", use: i, pin: p});
  S.pending = {use: i, pin: p};
  S.sel = {kind: "use", use: i};
  render();
  status("now click the header pin for " + p + " (Esc cancels)");
}

function chain(parts) { return h("div", {class: "chain"}, parts.filter(Boolean).join("  →  ")); }

function details() {
  const d = $("details");
  d.replaceChildren();
  const sel = S.sel;
  if (!sel) return;
  const ri = refIndex(), uses = S.setup.use || [];
  const pinText = (cid, key) => { const c = conn(cid), p = c && c.pins[key]; return c ? c.label + " pin " + key + (p ? " = " + p.ref + " = FPGA " + p.pin : "") : cid + "." + key; };

  if (sel.kind === "vbit" || sel.kind === "vport") {
    const p = ports().find((q) => q.capability === sel.cap && q.signal === sel.signal);
    if (!p) return;
    d.append(h("h4", {}, "design " + p.signal + (sel.kind === "vbit" ? "[" + sel.bit + "]" : "") + "  (" + p.capability + ", " + p.direction + ")"));
    for (const pr of p.providers) {
      const use = uses[pr.attach_index] || {};
      const bits = (pr.bits || []).filter((b) => sel.kind === "vport" || b.design_bit === sel.bit);
      if (sel.kind === "vbit" && pr.bits && !bits.length) continue;
      if (!pr.bits || !bits.some((b) => b.ref)) {
        const head = "design " + portName(p) + (sel.kind === "vbit" ? "[" + sel.bit + "]" : "");
        let shown = 0;
        if (use.module) {
          const m = moduleDef(use.module);
          for (const [mp, w] of Object.entries(wiresOf(use))) for (const l of linksOfPin(pr.attach_index, m.pins[mp])) if (l.p === p) {
            d.append(chain([head, l.via ? l.via + " (" + l.port_at + " … " + l.driver_port + ")" : "", useLabel(use) + " pin " + mp + " (" + m.pins[mp] + ")", pinText(...w.split("."))]));
            shown++;
          }
        }
        if (!shown) d.append(chain([head, useLabel(use) + " (" + pr.peripheral + ")", pr.via ? "via " + pr.via : "",
                                    Object.entries(pr.pins).map(([s, ps]) => s + " " + ps.map((x) => x.pin).join(",")).join("; ")]));
      } else {
        for (const b of bits) {
          const hp = b.ref && ri[b.ref];
          const mod = hp && uses[pr.attach_index] && uses[pr.attach_index].module
            ? Object.entries(wiresOf(uses[pr.attach_index])).filter(([, w]) => w === hp).map(([mp]) => "module pin " + mp)[0] : "";
          d.append(chain(["design " + p.signal + "[" + b.design_bit + "]", useLabel(use) + " bit " + b.provider_bit, mod,
                          hp ? pinText(...hp.split(".")) : (b.ref ? b.ref + " = FPGA " + b.pin : "")]));
        }
      }
    }
  } else if (sel.kind === "pin" || sel.kind === "wire") {
    const key = sel.kind === "pin" ? sel.conn + "." + sel.key : wiresOf(uses[sel.use])[sel.pin];
    const [cid, k] = key.split(".");
    const c = conn(cid), pin = c.pins[k];
    d.append(h("h4", {}, c.label + " pin " + k));
    edgeList(d, pin.ref);
    d.append(h("table", {}, h("tr", {}, h("td", {}, "pinmap"), h("td", {}, pin.ref)),
                          h("tr", {}, h("td", {}, "FPGA pin"), h("td", {}, pin.pin)),
                          h("tr", {}, h("td", {}, "voltage"), h("td", {}, (c.voltage || "?") + " V"))));
    let any = false;
    uses.forEach((u, i) => { if (u.module) for (const [p, w] of Object.entries(wiresOf(u))) if (w === key) {
      any = true;
      const m = moduleDef(u.module);
      d.append(chain([pinText(cid, k), useLabel(u) + " pin " + p + " (" + m.pins[p] + ")"]));
      if (!STATIC) d.append(h("button", {onclick: () => { const uu = S.setup.use[i]; if (uu.plug) { uu.wires = wiresOf(uu); delete uu.plug; } delete uu.wires[p]; S.sel = {kind: "use", use: i}; changed("disconnected " + p); }}, "Disconnect " + p));
    } });
    const gi = uses.findIndex((u) => u.gpio === cid);
    if (gi >= 0) any = true;
    if (!any) d.append(h("p", {}, "Not connected."));
  } else if (sel.kind === "edge") {
    const ed = ((S.ev && S.ev.trace && S.ev.trace.edges) || [])[sel.n];
    if (ed) connectionPanel(d, ed);
  } else if (sel.kind === "dseg" || sel.kind === "driver") {
    driverPanel(d, sel);
  } else if (sel.kind === "ref") {
    const o = S.board.onboard.find((x) => Object.values(x.pins).some((ps) => ps.some((pp) => pp.ref === sel.ref)));
    const pp = o && Object.entries(o.pins).flatMap(([s, ps]) => ps.map((x) => Object.assign({s}, x))).find((x) => x.ref === sel.ref);
    d.append(h("h4", {}, (o ? o.label + " " : "") + sel.ref));
    if (pp) d.append(h("div", {}, "signal " + pp.s + ", FPGA pin " + (pp.pin || "?")));
    const eds = ((S.ev && S.ev.trace && S.ev.trace.edges) || []).filter((ed) => ed.ref === sel.ref);
    edgeList(d, sel.ref);
    if (!eds.length) d.append(h("p", {}, "No design port reaches this pin."));
  } else if (sel.kind === "use") {
    useDetails(d, sel.use);
  } else if (sel.kind === "onboard") {
    const o = onboardDef(sel.id), i = uses.findIndex((u) => u.onboard === sel.id);
    d.append(h("h4", {}, o.label + " (" + o.attach.peripheral + ")"));
    if (i >= 0 && profileDrop(i)) d.append(h("p", {class: "note"}, "Not connected to the design: " + dropText(i) + "."));
    for (const [s, ps] of Object.entries(o.pins)) d.append(h("div", {}, s + ": " + ps.map((x) => x.ref + " = " + (x.pin || "?")).join(", ")));
    if (i >= 0) { for (const x of portsOfUse(i)) d.append(chain(["design " + x.port.signal + (x.bits.length ? "[" + x.bits.join(",") + "]" : ""), o.label + (x.via ? " via " + x.via : "")])); }
    if (!STATIC) {
      if (i >= 0) { paramForm(d, i); d.append(h("button", {onclick: () => { S.setup.use.splice(i, 1); changed("stopped using " + o.label); }}, "Do not use")); }
      else d.append(h("button", {onclick: () => { S.setup.use.push({onboard: o.id}); changed("using " + o.label); }}, "Use"));
    }
  } else if (sel.kind === "conn") {
    const c = conn(sel.id), i = uses.findIndex((u) => u.gpio === sel.id);
    d.append(h("h4", {}, c.label + " (" + c.type + ", " + (c.voltage || "?") + " V)"));
    const t = h("table", {});
    for (const row of c.rows) for (const key of row) {
      const p = c.pins[key];
      t.append(h("tr", {}, h("td", {}, key), h("td", {}, p ? p.ref : c.power[key] || ""), h("td", {}, p ? p.pin : "")));
    }
    d.append(t);
    if (!STATIC && c.bank) {
      if (i >= 0) d.append(h("button", {onclick: () => { S.setup.use.splice(i, 1); changed(c.label + " no longer design gpio"); }}, "Stop handing it to the design as gpio"));
      else d.append(h("button", {onclick: () => { S.setup.use.push({gpio: c.id, params: {width: Object.keys(c.pins).length}}); changed(c.label + " is design gpio"); }}, "Hand to the design as gpio"));
    }
  }
}

// everything about one design bit <-> pin connection, for the side panel
function connectionPanel(d, ed) {
  const p = ports().find((q) => q.design_port === ed.design_port) || {};
  const use = S.setup.use[ed.use] || {};
  const a = attachOf(ed.use) || {};
  const per = S.board.peripherals[a.peripheral] || {};
  const ri = refIndex(), hp = ri[ed.ref];
  const c = hp && conn(hp.split(".")[0]);
  const link = ed.signal ? linksOfPin(ed.use, ed.signal).find((l) => l.p === p) : null;
  const mod = use.module ? moduleDef(use.module) : null;
  const modPins = mod && hp ? Object.entries(wiresOf(use)).filter(([, w]) => w === hp).map(([mp]) => mp + " (" + mod.pins[mp] + ")") : [];
  const dir = p.direction === "hw_to_user" ? "input of design_top (the hardware drives it)"
            : p.direction === "user_to_hw" ? "output of design_top (the design drives the hardware)"
            : p.direction === "inout" ? "bidirectional" : (p.direction || "");
  const range = p.width > 1 ? "[" + (p.width - 1) + ":0]" : "";
  const active = (a.params || {}).active;
  const rows = [
    ["Connection", ed.via ? "through the " + ed.via + " driver" : "direct (no logic between the design bit and the pin)"],
    ["Design port", "design_top." + (ed.bit === null ? bitRange(ed.design_port, ed.bits || []) : ed.design_port + "[" + ed.bit + "]") +
                    " of " + ed.design_port + range],
    ["Capability", (p.capability || "?") + "." + (p.signal || "?")],
    ["Direction", dir],
    ["Provided by", useLabel(use) + (use.onboard ? " (on the board)" : use.module ? " (add-on module)" : "") +
                    " — peripheral " + (a.peripheral || "?") + (per.description ? ": " + per.description : "")],
  ];
  if (ed.via) {
    rows.push(["Driver", ed.via + (per.driver_file ? "  (" + per.driver_file + ")" : "")]);
    if (link) rows.push(["Driver ports", "design side ." + link.port_at + ", pin side ." + link.driver_port]);
    rows.push(["Bit relation", ed.bit === null ? wholePortText(ed, p)
                                               : "bit for bit (pin " + ed.signal + "[" + ed.bit + "] ↔ " + ed.design_port + "[" + ed.bit + "])"]);
  }
  rows.push(["Peripheral signal", (ed.signal || (p.signal || "")) + (a.peripheral ? " of " + a.peripheral : "")]);
  rows.push(["Pinmap entry", ed.ref]);
  rows.push(["FPGA pin", ed.pin || "?"]);
  if (c) rows.push(["Header pin", c.label + " pin " + hp.split(".")[1] + " (" + c.type + ", " + (c.voltage || "?") + " V)"]);
  else rows.push(["On the board", useLabel(use) + " (no header: the pin goes to the on-board part)"]);
  if (mod) rows.push(["Module pin", modPins.length ? modPins.join(", ") + " of " + mod.name : "not wired to this pin"]);
  if (mod && mod.verified === false) rows.push(["Module pinout", "not verified against a vendor document: " + (mod.source || "")]);
  if (active) rows.push(["Active level", active + (active === "low" ? " (the build inverts the bit)" : "")]);
  d.append(h("h4", {}, "design " + (ed.bit === null ? bitRange(ed.design_port, ed.bits || []) : ed.design_port + "[" + ed.bit + "]") + "  ↔  " + (ed.pin || ed.ref)));
  d.append(h("table", {class: "facts"}, ...rows.map(([k, v]) => h("tr", {}, h("th", {}, k), h("td", {}, v)))));
}

// a driver box, or the line from it to a pin or to design bits
function driverPanel(d, sel) {
  const dr = driverOf(sel);
  if (!dr) return;
  const all = traceEdges(), use = S.setup.use[dr.use] || {}, a = attachOf(dr.use) || {};
  const per = S.board.peripherals[a.peripheral] || {}, ri = refIndex();
  const where = (ed) => { const hp = ri[ed.ref], c = hp && conn(hp.split(".")[0]);
    return (c ? c.label + " pin " + hp.split(".")[1] + " = " : "") + ed.ref + " = FPGA " + (ed.pin || "?"); };
  const item = (n, text) => h("div", {class: "chain clickable", onclick: () => select({kind: "edge", n})}, text);
  const about = "The " + dr.via + " driver is logic the build places inside the FPGA between design_top and the pins of " +
                useLabel(use) + (per.driver_file ? " (" + per.driver_file + ")" : "") + ". Lines on its left go to design bits, " +
                "lines on its right to FPGA pins; the lines inside show which pin serves which design bits.";
  if (sel.kind === "driver") {
    d.append(h("h4", {}, dr.via + " — driver of " + useLabel(use)));
    d.append(h("p", {}, about));
    const rows = [["Peripheral", (a.peripheral || "?") + (per.description ? ": " + per.description : "")],
                  ["Its pins", [...dr.pin.values()].map((an) => an.label + " → " + where(all[an.edges[0]])).join("; ")],
                  ["Design bits it serves", [...dr.design.values()].map((an) => an.label).join(", ")]];
    d.append(h("table", {class: "facts"}, ...rows.map(([k, v]) => h("tr", {}, h("th", {}, k), h("td", {}, v)))));
    d.append(h("h4", {}, "Its connections"));
    for (const n of dr.edges) d.append(item(n, designLabel(all[n]) + "  ↔  " + pinLabel(all[n]) + " (" + all[n].ref + ")"));
    return;
  }
  const an = (sel.side === "pin" ? dr.pin : dr.design).get(sel.key);
  if (!an) return;
  if (sel.side === "pin") {
    d.append(h("h4", {}, dr.via + " pin " + an.label + "  ↔  " + where(all[an.edges[0]])));
    d.append(h("p", {}, an.edges.length > 1
      ? "Through this one pin the driver serves " + an.edges.length + " design ports. That is not an electrical fan-out: " +
        "the driver carries them all over the pin (as a serial bus, time-multiplexed, or as timing such as a clock, strobe or sync) " +
        "and connects each to the design inside the FPGA:"
      : "Through this pin the driver serves:"));
    for (const n of an.edges) d.append(item(n, designLabel(all[n]) + (all[n].bit === null && (all[n].bits || []).length > 1 ? "  (together)" : "")));
  } else {
    d.append(h("h4", {}, "design " + an.label + "  ↔  " + dr.via + " driver"));
    d.append(h("p", {}, an.edges.length > 1
      ? "The driver connects these design bits to " + an.edges.length + " of its pins; each pin carries them together " +
        "(data, clock, strobe or timing), not one pin per bit:"
      : "The driver connects these design bits to one pin:"));
    for (const n of an.edges) d.append(item(n, pinLabel(all[n]) + "  →  " + where(all[n])));
  }
  d.append(h("p", {class: "muted"}, about));
}

// the connections (design bit <-> pin edges) at one pinmap entry, grouped by
// the part that makes them, each clickable
function edgeList(d, ref) {
  const all = (S.ev && S.ev.trace && S.ev.trace.edges) || [];
  const mine = all.map((ed, n) => [ed, n]).filter(([ed]) => ed.ref === ref);
  if (!mine.length) return;
  d.append(h("h4", {}, "What this pin carries"));
  const byUse = new Map();
  for (const [ed, n] of mine) { if (!byUse.has(ed.use)) byUse.set(ed.use, []); byUse.get(ed.use).push([ed, n]); }
  for (const [u, list] of byUse) {
    const use = S.setup.use[u] || {}, via = list[0][0].via;
    const what = via ? (list.length > 1 ? "time-multiplexed by the " : "through the ") + via + " driver of " + useLabel(use)
                     : use.gpio ? "directly: " + useLabel(use) + " hands this pin to the design"
                     : "directly, for " + useLabel(use);
    d.append(h("div", {class: "muted"}, what + ":"));
    for (const [ed, n] of list)
      d.append(h("div", {class: "chain clickable", onclick: () => select({kind: "edge", n})},
                 ed.bit === null ? bitRange(ed.design_port, ed.bits || []) : ed.design_port + "[" + ed.bit + "]"));
  }
}

function useDetails(d, i) {
  const use = S.setup.use[i];
  d.append(h("h4", {}, useLabel(use) + (use.module ? " (" + moduleDef(use.module).peripheral + ")" : "")));
  if (profileDrop(i)) d.append(h("p", {class: "note"}, "Not connected to the design: " + dropText(i) + "."));
  if (use.module) {
    const m = moduleDef(use.module);
    if (m.verified === false) d.append(h("div", {class: "chain"}, "pinout not verified: " + (m.source || "")));
    for (const x of portsOfUse(i)) d.append(chain(["design " + x.port.signal + (x.bits.length ? "[" + x.bits.join(",") + "]" : ""), useLabel(use) + (x.via ? " via " + x.via : "")]));
    const w = wiresOf(use);
    const t = h("table", {});
    const free = [];
    for (const c of S.board.connectors) for (const key of Object.keys(c.pins)) free.push(c.id + "." + key);
    for (const [p, sig] of Object.entries(m.pins)) {
      if (passive(sig)) continue;
      let cell;
      if (STATIC) cell = h("td", {}, w[p] || "—");
      else {
        const s = h("select", {onchange: (e) => { if (use.plug) { use.wires = wiresOf(use); delete use.plug; } use.wires = use.wires || {};
                                                   if (e.target.value) use.wires[p] = e.target.value; else delete use.wires[p]; changed("rewired " + p); }},
                    h("option", {value: ""}, "— not wired"));
        for (const f of free) { const o = h("option", {value: f}, f + " (" + conn(f.split(".")[0]).pins[f.split(".")[1]].pin + ")"); if (w[p] === f) o.selected = true; s.append(o); }
        cell = h("td", {}, s);
      }
      t.append(h("tr", {}, h("td", {}, p), h("td", {}, sig), cell));
    }
    d.append(t);
    if (!STATIC) {
      d.append(h("div", {}, h("button", {onclick: () => autoWire(i)}, "Auto-wire"),
                 " picks free header pins (a Pmod module plugs into a free Pmod row)."));
      d.append(h("div", {class: "chain"}, "Or click a module pin in the drawing, then a header pin."));
    }
  } else if (use.raw) {
    d.append(h("pre", {}, JSON.stringify(use.raw, null, 1)));
  }
  if (!STATIC) {
    if (use.module) paramForm(d, i);
    d.append(h("div", {},
      h("button", {onclick: () => move(i, -1)}, "Move up"), h("button", {onclick: () => move(i, 1)}, "Move down"),
      h("button", {onclick: () => { S.setup.use.splice(i, 1); S.sel = null; changed("removed " + useLabel(use)); }}, "Remove")));
    d.append(h("div", {class: "chain"}, "Order decides which design bits a part gets when several provide the same port."));
  }
}

async function autoWire(i) {
  try {
    const got = await api("/api/autowire", {setup: S.setup, use: i});
    const use = S.setup.use[i];
    delete use.plug; delete use.wires;
    Object.assign(use, got);
    S.pending = null;
    S.sel = {kind: "use", use: i};
    await changed("auto-wired " + useLabel(use) + (got.plug ? " (plugged into " + got.plug.connector + " row " + got.plug.row + ")" : ""));
  } catch (e) { status(e.message, true); }
}

function move(i, delta) {
  const j = i + delta, u = S.setup.use;
  if (j < 0 || j >= u.length) return;
  [u[i], u[j]] = [u[j], u[i]];
  S.sel = {kind: "use", use: j};
  changed("moved");
}

// parameters of the peripheral a use attaches, editable
function paramForm(d, i) {
  const use = S.setup.use[i];
  const perId = use.module ? moduleDef(use.module).peripheral : use.onboard ? onboardDef(use.onboard).attach.peripheral : null;
  const per = perId && S.board.peripherals[perId];
  if (!per || !Object.keys(per.parameters).length) return;
  const base = use.onboard ? (onboardDef(use.onboard).attach.params || {}) : {};
  const t = h("table", {});
  for (const [k, def] of Object.entries(per.parameters)) {
    const cur = use.params && k in use.params ? use.params[k] : base[k];
    const inp = h("input", {value: cur === undefined ? "" : typeof cur === "object" ? JSON.stringify(cur) : String(cur),
                            placeholder: def && def.default !== undefined ? "default " + def.default : "", size: 12,
                            onchange: (e) => { const v = e.target.value.trim(); use.params = use.params || {};
                                               if (v === "") delete use.params[k];
                                               else { let x = v; try { x = JSON.parse(v); } catch (_) { /* a plain string */ } use.params[k] = x; }
                                               if (!Object.keys(use.params).length) delete use.params;
                                               changed(k + " = " + (v || "default")); }});
    t.append(h("tr", {}, h("td", {}, k), h("td", {}, inp), h("td", {}, (def && def.type) || "")));
  }
  d.append(h("h4", {}, "parameters"), t);
}

// ---------------------------------------------------------------- tables, problems

function tables() {
  const tb = $("connections").querySelector("tbody");
  tb.replaceChildren();
  for (const r of connectionRows()) {
    const same = S.sel && JSON.stringify(S.sel) === JSON.stringify(r.sel);
    const tr = h("tr", {class: same ? "sel" : "", onclick: () => select(r.sel)},
                 h("td", {}, r.design), h("td", {}, r.provider), h("td", {}, r.mpin), h("td", {}, r.signal || ""),
                 h("td", {}, r.header), h("td", {}, r.ref), h("td", {}, r.fpga));
    tb.append(tr);
  }
  const pl = $("problems");
  pl.replaceChildren();
  const probs = (S.ev && S.ev.problems) || [];
  if (S.ev && S.ev.profile)
    pl.append(h("li", {}, "The design-wiring profile " + S.ev.profile + " applies to this id: it fixes which design bits " +
                          "each part takes for the example designs. A part added here needs an entry there, or save under a new id."));
  for (const x of (S.ev && S.ev.excluded) || [])
    pl.append(h("li", {class: "warning"}, "not traced (the drawing leaves it out): " + useLabel(S.setup.use[x.use] || {}) + " — " + x.reason));
  if (!probs.length) pl.append(h("li", {}, "no problems"));
  for (const p of probs) pl.append(h("li", {class: p.level}, p.level + ": " + p.message));
  $("config-text").textContent = (S.ev && S.ev.configuration_text) || "";
}

function render() {
  // a selection whose part has been removed or moved away is dropped
  const n = (S.setup && S.setup.use || []).length;
  if (S.sel && ["use", "wire", "dseg", "driver"].includes(S.sel.kind) && !(S.sel.use < n)) S.sel = null;
  if (S.pending && !(S.pending.use < n)) S.pending = null;
  draw(); details(); tables(); designChoices(); renderTitle(); headerActions();
}

// the use a Remove in the header would take out: a selected module (or one of
// its wires) or a raw attach
function removableUse() {
  const sel = S.sel;
  if (!sel || !S.setup) return null;
  const i = sel.kind === "use" || sel.kind === "wire" ? sel.use : null;
  const use = i === null ? null : S.setup.use[i];
  return use && (use.module || use.raw) ? i : null;
}

function headerActions() {
  if (STATIC || !S.board) return;
  const add = $("add-module");
  if (add.options.length !== S.board.modules.length + 1) {
    add.replaceChildren(h("option", {value: ""}, "Add module…"),
                        ...S.board.modules.map((m) => h("option", {value: m.id}, m.name + " (" + m.peripheral + ")")));
  }
  const i = removableUse(), btn = $("remove-sel");
  btn.disabled = i === null;
  btn.textContent = i === null ? "Remove" : "Remove " + useLabel(S.setup.use[i]);
}

function removeSelected() {
  const i = removableUse();
  if (i === null) return;
  const label = useLabel(S.setup.use[i]);
  S.setup.use.splice(i, 1);
  S.sel = null; S.pending = null;
  changed("removed " + label);
}

// the design list: the designs this rig satisfies, selectable; the others
// greyed with what they need that the rig lacks (their `// requires:` block)
function designChoices() {
  const box = $("designs"), fit = S.ev && S.ev.designs;
  if (!S.board) return;
  const good = [], bad = [];
  for (const d of S.board.designs) {
    const unmet = fit ? fit[d] || [] : [];
    (unmet.length ? bad : good).push([d, unmet]);
  }
  if (!good.some(([d]) => d === S.design))
    S.design = good.some(([d]) => d === "1_06_binary_counter") ? "1_06_binary_counter" : good.length ? good[0][0] : null;
  const items = [h("div", {class: "designs-head"}, fit ? "Fit this rig (" + good.length + ")" : "Designs (fix the rig's problems to check them)")];
  for (const [d] of good)
    items.push(h("div", {class: "design fit" + (d === S.design ? " chosen" : ""), "data-design": d,
                         onclick: () => { S.design = d; designChoices(); }}, d));
  if (bad.length) items.push(h("div", {class: "designs-head"}, "Do not fit (" + bad.length + ")"));
  for (const [d, unmet] of bad)
    items.push(h("div", {class: "design unfit", "data-design": d, title: unmet.join("\n")}, d, h("span", {class: "why"}, unmet.join("; "))));
  box.replaceChildren(...items);
}

function staleWarning() {
  if (S.ev && S.ev.server_stale)
    status("unifpga's code changed since this server started: restart ./unifpga serve and reload the page", true);
}

function showTab(name) {
  for (const b of document.querySelectorAll(".tab")) b.classList.toggle("active", b.dataset.tab === name);
  $("tab-rig").hidden = name !== "rig";
  $("tab-config").hidden = name !== "config";
  if (name === "rig") applyView();
}

function renderTitle() {
  if (!S.setup) return;
  $("title").textContent = (S.setup.id || "(new setup)") + " — " + S.setup.board + " · " + (S.setup.toolchain || "") +
                           (S.dirty ? "  (not saved)" : "");
}

function renderModules() {
  const box = $("modules");
  box.replaceChildren(...S.board.modules.map((m) => h("div", {class: "module"},
    h("div", {class: "name"}, m.name), h("div", {class: "meta"}, "peripheral " + m.peripheral + " · " +
      Object.values(m.pins).filter((s) => !passive(s)).length + " signal pins" + (m.verified === false ? " · pinout not vendor-verified" : "")),
    STATIC ? null : h("button", {onclick: () => addModule(m.id)}, "Add"))));
}

function addModule(id) {
  S.setup.use.push({module: id, wires: {}});
  const i = S.setup.use.length - 1;
  const first = Object.entries(moduleDef(id).pins).find(([, s]) => !passive(s));
  S.sel = {kind: "use", use: i};
  showTab("rig");
  changed("added " + moduleDef(id).name).then(() => {
    if (first) { S.pending = {use: i, pin: first[0]}; render(); status("click the header pin for " + first[0] + ", or Auto-wire in the side panel (Esc cancels)"); }
  });
}

// ---------------------------------------------------------------- server

async function api(path, body) {
  const opt = body === undefined ? {} : {method: "POST", headers: {"Content-Type": "application/json", "X-Unifpga-Studio": "1"}, body: JSON.stringify(body)};
  const r = await fetch(path, opt);
  const data = r.headers.get("Content-Type").startsWith("application/json") ? await r.json() : null;
  if (!r.ok) throw new Error((data && data.error) || r.statusText);
  return data;
}

let evalSeq = 0;
async function changed(msg) {
  S.dirty = true;
  const seq = ++evalSeq;
  status(msg + " — checking…");
  render();
  try {
    const ev = await api("/api/evaluate", {setup: S.setup});
    if (seq !== evalSeq) return;
    S.ev = ev;
    S.lastDone = seq;
    status(msg + (S.dirty ? " (not saved)" : ""));
    staleWarning();
  } catch (e) { status(e.message, true); }
  render();
}

async function loadBoard(id, setupId) {
  S.board = await api("/api/board/" + encodeURIComponent(id));
  fillSelect($("setup"), S.board.setups, setupId);
  fillSelect($("toolchain"), S.board.toolchains);
  renderModules();
  const sid = setupId || S.board.setups[0];
  if (sid) await loadSetup(sid);
  else { S.setup = {id: "", board: id, toolchain: S.board.toolchains[0], use: []}; S.ev = null; render(); }
}

async function loadSetup(id) {
  S.setup = await api("/api/setup/" + encodeURIComponent(id));
  S.sel = null; S.pending = null; S.dirty = false; S.view = null;
  $("setup").value = id;
  $("toolchain").value = S.setup.toolchain;
  S.ev = await api("/api/evaluate", {setup: S.setup});
  status("loaded " + id);
  staleWarning();
  render();
}

function fillSelect(sel, values, current, label) {
  sel.replaceChildren(...values.map((v) => { const o = h("option", {value: v}, label ? label(v) : v); if (v === current) o.selected = true; return o; }));
}

function wire() {
  $("board").addEventListener("change", (e) => loadBoard(e.target.value).catch((x) => status(x.message, true)));
  $("setup").addEventListener("change", (e) => loadSetup(e.target.value).catch((x) => status(x.message, true)));
  $("toolchain").addEventListener("change", (e) => { S.setup.toolchain = e.target.value; changed("toolchain " + e.target.value); });
  for (const b of document.querySelectorAll(".tab")) b.addEventListener("click", () => showTab(b.dataset.tab));
  $("add-module").addEventListener("change", (e) => { const id = e.target.value; e.target.value = ""; if (id) addModule(id); });
  $("remove-sel").addEventListener("click", removeSelected);
  $("new-setup").addEventListener("click", () => {
    const id = prompt("New setup id (a-z, 0-9, _):", S.board.board + "_my_rig");
    if (!id) return;
    const copy = confirm("Start from the current setup? (Cancel starts empty with the board's clock)");
    const base = copy ? clone(S.setup) : {board: S.board.board, toolchain: $("toolchain").value, use: [{onboard: "clock"}]};
    base.id = id; delete base.notes;
    S.setup = base; S.sel = null;
    $("setup").append(h("option", {value: id}, id)); $("setup").value = id;
    changed("new setup " + id);
  });
  $("save").addEventListener("click", async () => {
    try { const r = await api("/api/save", {setup: S.setup}); S.dirty = false; renderTitle(); status("saved " + r.setup + " and " + r.configuration); }
    catch (e) { status(e.message, true); }
  });
  $("project").addEventListener("click", async () => {
    const out = $("project-result");
    try {
      if (S.dirty) throw new Error("save the setup first");
      if (!S.design) throw new Error("no design fits this rig");
      out.textContent = "generating the project for " + S.design + "…";
      const r = await api("/api/project", {setup_id: S.setup.id, design: S.design});
      const link = h("a", {href: "/api/project/" + encodeURIComponent(S.setup.id) + "/" + encodeURIComponent(S.design) + ".zip"}, "download zip");
      out.replaceChildren((r.ok ? "written to " : "written, with errors, to ") + r.output + " (" + r.files.length + " files) — ", link);
    } catch (e) { out.textContent = e.message; }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { closePicker(); S.pending = null; S.sel = null; status(""); render(); }
    const typing = /^(INPUT|SELECT|TEXTAREA)$/.test((document.activeElement || {}).tagName || "");
    if ((e.key === "Delete" || e.key === "Backspace") && !typing && removableUse() !== null) { e.preventDefault(); removeSelected(); }
  });
}

async function main() {
  wireZoomPan();
  if (STATIC) {
    document.body.classList.add("readonly");
    S.board = STATIC.board; S.setup = STATIC.setup; S.ev = STATIC.evaluation;
    fillSelect($("board"), [S.board.board]); fillSelect($("setup"), [S.setup.id]);
    for (const b of document.querySelectorAll(".tab")) b.addEventListener("click", () => showTab(b.dataset.tab));
    renderModules();
    status("read-only view (./unifpga serve to edit)");
    render();
    return;
  }
  wire();
  const boards = await api("/api/boards");
  fillSelect($("board"), boards);
  const q = new URLSearchParams(location.search);
  let b = q.get("board");
  if (!b && q.get("setup")) b = (await api("/api/setup/" + encodeURIComponent(q.get("setup")))).board;
  if (!boards.includes(b)) b = boards[0];
  $("board").value = b;
  await loadBoard(b, q.get("setup"));
  if (q.get("selftest")) return selftest();
  if (q.get("sel")) { S.sel = parseSel(q.get("sel")); render(); }
  if (q.get("tab")) showTab(q.get("tab"));
}

// ?sel=vbit:leds:led:2 | vport:leds:led | pin:jd:7 | wire:11:CLK | use:11 | onboard:leds | conn:ck
//      | driver:11:tm1638_board_controller | dseg:11:tm1638_board_controller:pin:dio|arduino_io[27]
function parseSel(text) {
  const [kind, a, b, c] = text.split(":");
  if (kind === "vbit") return {kind, cap: a, signal: b, bit: Number(c)};
  if (kind === "vport") return {kind, cap: a, signal: b};
  if (kind === "pin") return {kind, conn: a, key: b};
  if (kind === "wire") return {kind, use: Number(a), pin: b};
  if (kind === "use") return {kind, use: Number(a)};
  if (kind === "onboard" || kind === "conn") return {kind, id: a};
  if (kind === "ref") return {kind, ref: text.slice(4)};
  if (kind === "edge") return {kind, n: Number(a)};
  if (kind === "driver") return {kind, use: Number(a), via: b};
  if (kind === "dseg") return {kind, use: Number(a), via: b, side: c, key: text.split(":").slice(4).join(":")};
  return null;
}

main().catch((e) => status(e.message, true));

// ---------------------------------------------------------------- self test (?selftest=1)
// Drives the page's own edit handlers against the server and reports into
// <pre id="selftest">: add a module, wire it pin by pin, rewire, disconnect,
// toggle an on-board device and a gpio header, save under a scratch id and
// generate a project. For headless checks (tests/test_setup.py documents it).

async function settle() { await changedDone(); }
function changedDone() { return new Promise((r) => { const t0 = evalSeq; const tick = () => (S.lastDone === t0 ? r() : setTimeout(tick, 20)); tick(); }); }

async function selftest() {
  const log = [];
  const ok = (name, cond) => log.push((cond ? "PASS " : "FAIL ") + name);
  const errors = () => (S.ev.problems || []).filter((p) => p.level === "error").map((p) => p.message);
  try {
    // zoom and pan
    const svg = $("svg"), box = svg.getBoundingClientRect();
    const at = {clientX: box.left + box.width * 0.3, clientY: box.top + box.height * 0.3};
    const before = svgPoint(at);
    svg.dispatchEvent(new WheelEvent("wheel", Object.assign({deltaY: -300, bubbles: true, cancelable: true}, at)));
    const after = svgPoint(at);
    ok("scrolling up zooms in", S.view && S.view.w < S.content.w);
    ok("zoom keeps the point under the cursor", Math.abs(after.x - before.x) < 1 && Math.abs(after.y - before.y) < 1);
    const v0 = Object.assign({}, S.view), sel0 = S.sel;
    const pe = (type, dx) => svg.dispatchEvent(new PointerEvent(type, {clientX: at.clientX + dx, clientY: at.clientY,
                                                                      button: 0, pointerId: 1, bubbles: true}));
    pe("pointerdown", 0); pe("pointermove", 40); pe("pointermove", 120); pe("pointerup", 120);
    svg.dispatchEvent(new MouseEvent("click", {bubbles: true, clientX: at.clientX + 120, clientY: at.clientY}));
    ok("dragging pans", S.view.x < v0.x && S.view.w === v0.w);
    ok("a drag is not a click", S.sel === sel0);
    $("zoom-fit").click();
    ok("fit shows everything again", S.view === null && $("zoom-level").textContent === "100%");
    // driver-mediated trace (PmodVGA on arty_a7_35_pmod_mic3 style rigs)
    const vi = S.setup.use.findIndex((u) => u.module === "digilent_pmod_vga");
    if (vi >= 0) {
      select({kind: "wire", use: vi, pin: "R0"});
      const hiW = highlight();
      ok("a VGA pin lights the design bit it serves", hiW.vbits.has("screen.red.0") && ![...hiW.vbits].some((b) => b.startsWith("screen.green")));
      select({kind: "vport", cap: "screen", signal: "red"});
      const hiP = highlight();
      ok("the design's red lights exactly its four pins", ["R0", "R1", "R2", "R3"].every((p) => hiP.wires.has(vi + "." + p)) &&
         [...hiP.wires].filter((w) => w.startsWith(vi + ".")).length === 4);
      ok("the trace names the driver ports", $("details").textContent.includes("vga (red … vga_r)"));
      const edgesNow = S.ev.trace.edges;
      const n1 = edgesNow.findIndex((ed) => ed.design_port === "red" && ed.bit === 1);
      select({kind: "edge", n: n1});
      const txt = $("details").textContent;
      ok("a connection's panel gives its driver, ports, pinmap, FPGA and header pin",
         txt.includes("through the vga driver") && txt.includes("design side .red, pin side .vga_r") &&
         txt.includes("pmod_jb[5]") && txt.includes("JB pin 8") && txt.includes("Module pinR1 (r[1])"));
      const nl = edgesNow.findIndex((ed) => ed.design_port === "led" && ed.bit === 2 && !ed.via);
      if (nl >= 0) { select({kind: "edge", n: nl}); ok("a direct connection says so", $("details").textContent.includes("direct (no logic")); }
    }
    // drivers: a box each; a pin line lights exactly the connections it carries
    const drs = drivers();
    const shown = (sel) => !!document.querySelector("[data-sel='" + JSON.stringify(sel) + "']");
    if (drs.length) {
      ok("every driver is drawn as a box", drs.every((x) => shown({kind: "driver", use: x.use, via: x.via})));
      ok("no driver connection runs straight from a pin to the design",
         traceEdges().every((ed, n) => !ed.via || [...drs.find((x) => x.use === ed.use && x.via === ed.via).edges].includes(n)));
      const busiest = drs.flatMap((x) => [...x.pin.values()].map((an) => [x, an])).sort((p, q) => q[1].edges.length - p[1].edges.length)[0];
      const [bd, ban] = busiest;
      select({kind: "dseg", use: bd.use, via: bd.via, side: "pin", key: ban.key});
      const hd = highlight();
      ok("a driver pin line lights just the connections it carries", hd.edges.size === ban.edges.length && ban.edges.every((n) => hd.edges.has(n)));
      if (ban.edges.length > 1) ok("a pin serving several design ports is explained, not drawn as fan-out", $("details").textContent.includes("not an electrical fan-out"));
      select({kind: "driver", use: bd.use, via: bd.via});
      ok("a driver's panel lists its pins and design bits", $("details").textContent.includes("Its pins") && $("details").textContent.includes(bd.via));
      ok("the legend names the drivers present", $("svg").textContent.includes(bd.via + ")") || $("svg").textContent.includes(bd.via + ","));
    }
    // parts the design-wiring profile leaves out are marked and explained
    for (const x of (S.ev.profile_drops || []).slice(0, 1)) {
      select({kind: "use", use: x.use});
      ok("a part the profile leaves out says so", $("details").textContent.includes("Not connected to the design"));
    }
    // overlapping targets: a design bit whose line starts at it
    S.sel = null; render();
    // a line leaves its bit to the right, over the next bit's cell
    const cellOf = (ed, b) => { const p = ports().find((q) => q.design_port === ed.design_port);
      return p && document.querySelector('[data-sel=\'' + JSON.stringify({kind: "vbit", cap: p.capability, signal: p.signal, bit: b}) + '\']'); };
    const withEdge = (S.ev.trace.edges || []).find((ed) => ed.bit !== null && cellOf(ed, ed.bit + 1) &&
      Math.abs(cellOf(ed, ed.bit + 1).getBoundingClientRect().top - cellOf(ed, ed.bit).getBoundingClientRect().top) < 2);
    if (withEdge) {
      const cell = cellOf(withEdge, withEdge.bit + 1);
      const rb = cell.getBoundingClientRect();
      const pt = {clientX: rb.left + rb.width / 2, clientY: rb.top + rb.height / 2, bubbles: true};
      const kinds = candidatesAt(pt.clientX, pt.clientY).map((s) => s.kind);
      ok("a bit and its connection overlap there", kinds.includes("vbit") && kinds.some((k) => CONNECTIONS.has(k)));
      $("svg").dispatchEvent(new MouseEvent("click", pt));
      ok("a plain click on both asks which one", !!$("picker") && $("picker").textContent.includes(MODS.points + "-click picks"));
      closePicker();
      $("svg").dispatchEvent(new MouseEvent("click", Object.assign({[MODS.pointsKey]: true}, pt)));
      ok(MODS.points + "-click picks the bit", S.sel && S.sel.kind === "vbit" && !$("picker"));
      $("svg").dispatchEvent(new MouseEvent("click", Object.assign({[MODS.connsKey]: true}, pt)));
      ok(MODS.conns + "-click picks the connection", S.sel && CONNECTIONS.has(S.sel.kind));
    }
    S.setup.id = "selftest_rig"; delete S.setup.notes;       // a fresh rig: no design-wiring profile
    S.setup.use.push({module: "tm1638_led_key", wires: {CLK: S.board.connectors[0].id + ".99"}});
    await changed("broken"); await settle();
    ok("a broken module leaves the rest traced", !!S.ev.trace && S.ev.excluded.length === 1 && ports().length > 0);
    ok("the untraced module is named in Problems", $("problems").textContent.includes("not traced"));
    await autoWire(S.setup.use.length - 1); await settle();
    ok("auto-wire wires every signal pin", Object.keys(S.setup.use[S.setup.use.length - 1].wires || {}).length === 3 &&
       !S.ev.excluded.length && !(S.ev.problems || []).some((p) => p.level === "error"));
    S.setup.use.pop(); await changed("undo"); await settle();
    await changed("copy"); await settle();
    const n = S.setup.use.length;
    const addSel = $("add-module");
    addSel.value = "tm1638_led_key"; addSel.dispatchEvent(new Event("change")); await settle(); await settle();
    showTab("rig");
    ok("added module is listed", S.setup.use.length === n + 1);
    ok("unwired module reports its required signals", errors().some((m) => m.includes("required signal")));
    const i = S.setup.use.length - 1;
    // pins the server calls free (it knows pins the LCD connector shares), wired by clicking
    const suggestion = await api("/api/autowire", {setup: S.setup, use: i});
    const target = ["STB", "CLK", "DIO"].map((p) => suggestion.wires[p].split("."));
    for (const [p, [c, k]] of ["STB", "CLK", "DIO"].map((p, j) => [p, target[j]])) { modulePinClicked(i, p); pinClicked(c, k); await settle(); }
    ok("wired by clicking module pin then header pin", Object.keys(S.setup.use[i].wires).length === 3);
    ok("the new board adds design LEDs, traced to it", errors().length === 0 &&
       portsOfUse(i).some((x) => x.port.capability === "leds" && x.bits.length === 8));
    const [c0, k0] = target[0];
    pinClicked(c0, k0); await settle();
    ok("selecting a header pin shows its wire", $("details").textContent.includes("pin STB"));
    delete S.setup.use[i].wires["STB"]; await changed("disconnect"); await settle();
    ok("disconnecting brings back the required-signal error", errors().some((m) => m.includes("required signal 'stb'")));
    select({kind: "use", use: i});
    ok("the header's Remove names the selected module", !$("remove-sel").disabled && $("remove-sel").textContent.includes("TM1638"));
    $("remove-sel").click(); await settle();
    ok("removing the module restores the rig", S.setup.use.length === n && $("remove-sel").disabled);
    const ob = S.board.onboard.find((o) => S.setup.use.some((u) => u.onboard === o.id && o.id !== "clock"));
    const oi = S.setup.use.findIndex((u) => u.onboard === ob.id);
    S.setup.use.splice(oi, 1); await changed("unuse"); await settle();
    ok("an on-board device can be dropped", !S.setup.use.some((u) => u.onboard === ob.id));
    const r = await api("/api/save", {setup: S.setup});
    ok("saved", r.configuration.endsWith("selftest_rig.yml"));
    S.dirty = false;
    const pr = await api("/api/project", {setup_id: "selftest_rig", design: "1_06_binary_counter"});
    ok("project generated with a top", pr.ok && pr.files.includes("top.sv"));
    const misfit = Object.entries(S.ev.designs || {}).find(([, unmet]) => unmet.length);
    const unfitEl = misfit && document.querySelector('#designs .design.unfit[data-design="' + misfit[0] + '"] .why');
    if (misfit) ok("a design this rig cannot run is greyed with its reason", !!unfitEl && unfitEl.textContent === misfit[1].join("; "));
    ok("only fitting designs are selectable", [...document.querySelectorAll("#designs .design.fit")]
       .every((e) => !(S.ev.designs[e.dataset.design] || []).length));
    if (misfit) {
      try { await api("/api/project", {setup_id: "selftest_rig", design: misfit[0]}); ok("an unfit design is refused", false); }
      catch (e) { ok("an unfit design is refused", e.message.includes("does not fit")); }
    }
  } catch (e) { log.push("FAIL exception: " + e.message); }
  document.body.append(h("pre", {id: "selftest"}, log.join("\n")));
}

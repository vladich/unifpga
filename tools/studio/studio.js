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

// one design bit <-> pin edge, spelled out
function edgeText(ed) {
  const ri = refIndex(), hp = ri[ed.ref], c = hp && conn(hp.split(".")[0]);
  const where = hp ? c.label + " pin " + hp.split(".")[1] + " = " + ed.ref : ed.ref;
  const use = S.setup.use[ed.use];
  return "design " + ed.design_port + (ed.bit === null ? "" : "[" + ed.bit + "]") +
         (ed.via ? "  →  " + ed.via + " (driver)" : "  →  directly") +
         "  →  " + where + " = FPGA " + (ed.pin || "?") + (use ? "   [" + useLabel(use) + (ed.signal ? " " + ed.signal : "") + "]" : "");
}

// "design red ← vga red … vga_r" for one link
function linkText(l) {
  return "design " + portName(l.p) + (l.via ? "  →  " + l.via + " (" + l.port_at + " … " + l.driver_port + ")" : "");
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
      const links = linksOfPin(i, m.pins[p]);
      const prov = links.length ? [...new Set(links.map((l) => portName(l.p) + (l.via ? " (via " + l.via + ")" : "")))].join(" ")
                                : portsOfUse(i).map((x) => portName(x.port) + (x.bits.length ? "[" + x.bits.join(",") + "]" : "")).join(" ");
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
  const VX = 12, BX = 330, top = 16, OW = 290;

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
    g.addEventListener("click", () => select({kind: "vport", cap: p.capability, signal: p.signal}));
    svg.append(g);
    const cells = Math.max(width, 1), per = 14;
    for (let b = 0; b < cells; b++) {
      const cx = VX + 118 + (b % per) * 13, cy = y + Math.floor(b / per) * 14;
      const on = hi.vbits.has(p.capability + "." + p.signal + "." + b) || selPort;
      const r = el("rect", {x: cx, y: cy, width: 11, height: 11, rx: 2, class: "clickable",
                           fill: on ? "var(--sel)" : none ? "#f8f9fa" : "#e7f5ff", stroke: none ? "#ced4da" : "#1c7ed6"});
      r.append(el("title", {}, portName(p) + (width > 1 ? "[" + b + "]" : "") + "  (" + p.capability + "." + p.signal + ")"));
      S.pos.cells[portName(p) + "." + b] = {x: cx + 11, y: cy + 5.5};
      r.addEventListener("click", (e) => { e.stopPropagation();
        select(perBit ? {kind: "vbit", cap: p.capability, signal: p.signal, bit: b} : {kind: "vport", cap: p.capability, signal: p.signal}); });
      svg.append(r);
    }
    const rowsUsed = Math.max(Math.ceil(cells / per), 2) - 0.6;          // room for the capability name
    portRows.push({p, x: VX + 118 + Math.min(cells, per) * 13, y: y + 6});
    S.pos.rows[portName(p)] = {x: VX + 118 + Math.min(cells, per) * 13, y: y + 6};
    y += 14 * rowsUsed + 8;
  }
  if (!ports().length) svg.append(el("text", {x: VX, y: y + 10, fill: "#868e96"}, "(nothing in the rig can be traced yet)"));

  // board frame, on-board devices
  const used = new Map();
  (S.setup.use || []).forEach((u, i) => { if (u.onboard) used.set(u.onboard, i); });
  let oy = top + 40;
  const boardItems = [];
  for (const o of S.board.onboard) {
    const i = used.has(o.id) ? used.get(o.id) : null;
    const on = i !== null && hi.uses.has(i), selected = S.sel && S.sel.kind === "onboard" && S.sel.id === o.id;
    const g = el("g", {class: "clickable"});
    const obpins = i === null ? [] : Object.entries(o.pins).flatMap(([s, ps]) => ps.map((pp) => Object.assign({s}, pp)));
    const perRow = Math.floor((OW - 20) / 11), pinRows = Math.ceil(obpins.length / perRow);
    const boxH = 20 + pinRows * 11;
    g.append(el("rect", {x: BX + 12, y: oy, width: OW, height: boxH, rx: 3,
                        fill: i !== null ? "#d3f9d8" : "#ffffff", stroke: on || selected ? "var(--sel)" : "#868e96",
                        "stroke-width": on || selected ? 2.5 : 1}));
    obpins.forEach((pp, k) => {
      const px = BX + 22 + (k % perRow) * 11, py = oy + 24 + Math.floor(k / perRow) * 11;
      S.pos.obpins[pp.ref] = {x: px, y: py};
      const lit = hi.refs.has(pp.ref);
      const dot = el("circle", {cx: px, cy: py, r: 4, fill: lit ? "var(--sel)" : "#ffffff", stroke: lit ? "var(--sel)" : "#2b8a3e"});
      dot.append(el("title", {}, o.label + ": " + pp.s + " = " + pp.ref + " = FPGA " + (pp.pin || "?")));
      dot.addEventListener("click", (e) => { e.stopPropagation(); select({kind: "ref", ref: pp.ref}); });
      g.append(dot);
    });
    const lt = el("text", {x: BX + 18, y: oy + 14, "font-size": 12, fill: i === null ? "#868e96" : "#212529"}, o.label);
    lt.append(el("title", {}, o.label + (i === null ? " — not used by this setup" : "")));
    g.append(lt);
    g.addEventListener("click", () => select({kind: "onboard", id: o.id}));
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
    lbl.addEventListener("click", () => select({kind: "conn", id: c.id}));
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
      if (pin) circ.addEventListener("click", (e) => { e.stopPropagation(); pinClicked(c.id, key); });
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
    box.addEventListener("click", () => select({kind: "use", use: i}));
    g.append(box);
    const title = el("text", {x: MX + 8, y: my + 17, "font-weight": "bold", "font-size": 12, fill: col, class: "clickable"}, useLabel(use));
    title.addEventListener("click", () => select({kind: "use", use: i}));
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
      t.addEventListener("click", (e) => { e.stopPropagation(); modulePinClicked(i, p); });
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
    hit.addEventListener("click", () => select({kind: "wire", use: wdef.use, pin: wdef.pin}));
    const path = el("path", {d, fill: "none", stroke: isSel ? "var(--sel)" : wdef.col, "stroke-width": isSel ? 3.5 : 1.6,
                             opacity: S.sel && !isSel ? 0.35 : 0.9});
    path.append(el("title", {}, useLabel(S.setup.use[wdef.use]) + " " + wdef.pin + " → " + wdef.to));
    svg.append(path);
    svg.append(hit);
  }

  // design bit -> pin edges (tools/trace.py edges): to an on-board device's pin
  // dot or a header pin; faint, the selection's in red
  const ri = refIndex();
  ((S.ev && S.ev.trace && S.ev.trace.edges) || []).forEach((ed, n) => {
    const from = ed.bit === null ? S.pos.rows[ed.design_port] : S.pos.cells[ed.design_port + "." + ed.bit];
    const to = ri[ed.ref] ? S.pos.pins[ri[ed.ref]] : S.pos.obpins[ed.ref];
    if (!from || !to) return;
    const isSel = hi.edges.has(n);
    const d = "M" + from.x + "," + from.y + " C" + (from.x + 90) + "," + from.y + " " + (to.x - 90) + "," + to.y + " " + (to.x - 6) + "," + to.y;
    const path = el("path", {d, fill: "none", stroke: isSel ? "var(--sel)" : ed.via ? "#4c6ef5" : "#2f9e44",
                             "stroke-width": isSel ? 2.4 : 1.2, opacity: isSel ? 1 : S.sel ? 0.18 : 0.55,
                             "stroke-dasharray": ed.via ? "6 3" : ""});
    const hit = el("path", {d, fill: "none", stroke: "transparent", "stroke-width": 9, class: "clickable"});
    hit.append(el("title", {}, edgeText(ed)));
    hit.addEventListener("click", (e) => { e.stopPropagation(); select({kind: "edge", n}); });
    svg.append(path);
    svg.append(hit);
  });

  // legend
  const LY = Math.max(top + boardH, my, y) + 14;
  const legend = [["#2f9e44", "", "design bit wired straight to a pin"],
                  ["#4c6ef5", "6 3", "design port reaching a pin through a driver in the FPGA (TM1638, VGA, SPI ...)"],
                  ["#d9480f", "", "wire from a header pin to a module (one colour per module)"]];
  legend.forEach(([col, dash, text], k) => {
    const ly = LY + k * 16;
    svg.append(el("path", {d: "M" + VX + "," + ly + " l40,0", stroke: col, "stroke-width": 2, "stroke-dasharray": dash}));
    svg.append(el("text", {x: VX + 48, y: ly + 4, "font-size": 11, fill: "#495057"}, text));
  });
  svg.append(el("text", {x: VX, y: LY + 3 * 16 + 4, "font-size": 11, fill: "#868e96"},
                "Click any line, pin, bit or part: the panel on the right says what the connection is."));

  const W = MX + 260, H = LY + 4 * 16 + 14;
  S.content = {w: W, h: H};
  applyView();
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
    if (ed.bit === null) hi.vports.add(p.capability + "." + p.signal);
    else hi.vbits.add(p.capability + "." + p.signal + "." + ed.bit);
  };
  const edgesWhere = (f) => allEdges.forEach((ed, n) => { if (f(ed)) takeEdge(ed, n); });
  const ri = refIndex();
  // the module pins of use i that serve design port p (through a driver)
  const addServingPins = (i, p) => {
    hi.uses.add(i);
    const use = S.setup.use[i];
    if (!use || !use.module) return;
    const m = moduleDef(use.module);
    for (const [mp, w] of Object.entries(wiresOf(use)))
      if (linksOfPin(i, m.pins[mp]).some((l) => l.p === p)) { hi.wires.add(i + "." + mp); hi.mpins.add(i + "." + mp); hi.pins.add(w); }
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
  if (sel.kind === "edge") {
    const ed = allEdges[sel.n];
    if (ed) {
      takeEdge(ed, sel.n);
      hi.uses.add(ed.use);
      const hp = ri0[ed.ref], use = S.setup.use[ed.use];
      if (hp && use && use.module) for (const [mp, w] of Object.entries(wiresOf(use))) if (w === hp) { hi.wires.add(ed.use + "." + mp); hi.mpins.add(ed.use + "." + mp); }
    }
  } else if (sel.kind === "ref") {
    edgesWhere((ed) => ed.ref === sel.ref);
    hi.refs.add(sel.ref);
  } else if (sel.kind === "vbit") {
    const p = ports().find((q) => q.capability === sel.cap && q.signal === sel.signal);
    if (p) { addBit(p, sel.bit); edgesWhere((ed) => ed.design_port === p.design_port && (ed.bit === sel.bit || ed.bit === null)); }
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
      for (const l of linksOfPin(i, moduleDef(u.module).pins[p])) { hi.vports.add(l.port); hi.links.add(l.port + "#" + i); }
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
      const bits = designBitsOfRef(pin.ref).filter((b) => b.use === i).map((b) => "design " + portName(b.port) + "[" + b.bit + "]");
      const links = linksOfPin(i, m.pins[p]);
      if (bits.length || !links.length) d.append(chain([bits.join(", ") || "(no design port)", useLabel(u) + " pin " + p + " (" + m.pins[p] + ")", pinText(cid, k)]));
      for (const l of links) d.append(chain([linkText(l), useLabel(u) + " pin " + p + " (" + m.pins[p] + ")", pinText(cid, k)]));
      if (!STATIC) d.append(h("button", {onclick: () => { const uu = S.setup.use[i]; if (uu.plug) { uu.wires = wiresOf(uu); delete uu.plug; } delete uu.wires[p]; S.sel = {kind: "use", use: i}; changed("disconnected " + p); }}, "Disconnect " + p));
    } });
    const gi = uses.findIndex((u) => u.gpio === cid);
    if (gi >= 0) { any = true; d.append(chain(designBitsOfRef(pin.ref).map((b) => "design " + b.port.signal + "[" + b.bit + "]").concat(["gpio " + c.label])) ); }
    if (!any) d.append(h("p", {}, "Not connected."));
  } else if (sel.kind === "edge") {
    const ed = ((S.ev && S.ev.trace && S.ev.trace.edges) || [])[sel.n];
    if (ed) connectionPanel(d, ed);
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
    ["Design port", "design_top." + ed.design_port + (ed.bit === null ? range + " (the whole port)" : "[" + ed.bit + "] of " + ed.design_port + range)],
    ["Capability", (p.capability || "?") + "." + (p.signal || "?")],
    ["Direction", dir],
    ["Provided by", useLabel(use) + (use.onboard ? " (on the board)" : use.module ? " (add-on module)" : "") +
                    " — peripheral " + (a.peripheral || "?") + (per.description ? ": " + per.description : "")],
  ];
  if (ed.via) {
    rows.push(["Driver", ed.via + (per.driver_file ? "  (" + per.driver_file + ")" : "")]);
    if (link) rows.push(["Driver ports", "design side ." + link.port_at + ", pin side ." + link.driver_port]);
    rows.push(["Bit relation", ed.bit === null ? "none: the pin serves the port as a whole (e.g. a sync or a serial line)"
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
  d.append(h("h4", {}, "design " + ed.design_port + (ed.bit === null ? "" : "[" + ed.bit + "]") + "  ↔  " + (ed.pin || ed.ref)));
  d.append(h("table", {class: "facts"}, ...rows.map(([k, v]) => h("tr", {}, h("th", {}, k), h("td", {}, v)))));
}

// the connections (design bit <-> pin edges) at one pinmap entry, clickable
function edgeList(d, ref) {
  const all = (S.ev && S.ev.trace && S.ev.trace.edges) || [];
  const mine = all.map((ed, n) => [ed, n]).filter(([ed]) => ed.ref === ref);
  if (!mine.length) return;
  d.append(h("h4", {}, "Connections at this pin"));
  for (const [ed, n] of mine)
    d.append(h("div", {class: "chain clickable", onclick: () => select({kind: "edge", n})}, edgeText(ed)));
}

function useDetails(d, i) {
  const use = S.setup.use[i];
  d.append(h("h4", {}, useLabel(use) + (use.module ? " (" + moduleDef(use.module).peripheral + ")" : "")));
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
  if (S.sel && (S.sel.kind === "use" || S.sel.kind === "wire") && !(S.sel.use < n)) S.sel = null;
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
    if (e.key === "Escape") { S.pending = null; S.sel = null; status(""); render(); }
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
      ok("a VGA pin lights the design port it serves", hiW.vports.has("screen.red") && !hiW.vports.has("screen.green"));
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
    ok("a design this rig cannot run is greyed with its reason", !!unfitEl && unfitEl.textContent === misfit[1].join("; "));
    ok("only fitting designs are selectable", [...document.querySelectorAll("#designs .design.fit")]
       .every((e) => !(S.ev.designs[e.dataset.design] || []).length));
    try { await api("/api/project", {setup_id: "selftest_rig", design: misfit[0]}); ok("an unfit design is refused", false); }
    catch (e) { ok("an unfit design is refused", e.message.includes("does not fit")); }
  } catch (e) { log.push("FAIL exception: " + e.message); }
  document.body.append(h("pre", {id: "selftest"}, log.join("\n")));
}

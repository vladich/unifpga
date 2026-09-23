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
// a port's name in design_top; the capability is added where two share a name
// (audio_in.sample, audio_out.sample)
function portName(p) {
  const same = ports().filter((q) => q.signal === p.signal).length > 1;
  return same ? p.capability + "." + p.signal : p.signal;
}

// design bits a pinmap ref carries, with the port and the providing use
function designBitsOfRef(ref) {
  const out = [];
  for (const p of ports()) for (const pr of p.providers) for (const b of pr.bits || [])
    if (b.ref === ref && b.design_bit !== null) out.push({port: p, use: pr.attach_index, bit: b.design_bit});
  return out;
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
      const prov = portsOfUse(i).map((x) => portName(x.port) + (x.bits.length ? "[" + x.bits.join(",") + "]" : "")).join(" ");
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
  S.pos = {pins: {}, uses: {}, ports: {}, mpins: {}};
  const hi = highlight();
  const VX = 12, BX = 330, top = 16, OW = 290;

  // virtual device
  let y = top + 24;
  svg.append(el("text", {x: VX, y: top + 8, "font-weight": "bold", "font-size": 14}, "Virtual device (design_top)"));
  const portRows = [];
  for (const p of ports()) {
    let width = 1;
    for (const pr of p.providers) for (const b of pr.bits || []) if (b.design_bit !== null) width = Math.max(width, b.design_bit + 1);
    const perBit = p.providers.some((pr) => pr.bits);
    const g = el("g", {class: "clickable"});
    const selPort = S.sel && S.sel.kind === "vport" && S.sel.signal === p.signal && S.sel.cap === p.capability;
    g.append(el("text", {x: VX, y: y + 11, "font-size": 12, fill: selPort ? "var(--sel)" : "#212529"},
                portName(p) + (perBit ? "[" + (width - 1) + ":0]" : "")));
    g.append(el("title", {}, p.capability + "." + p.signal + " (" + (p.direction || "") + ")"));
    g.addEventListener("click", () => select({kind: "vport", cap: p.capability, signal: p.signal}));
    svg.append(g);
    const cells = perBit ? width : 1, per = 14;
    for (let b = 0; b < cells; b++) {
      const cx = VX + 118 + (b % per) * 13, cy = y + Math.floor(b / per) * 14;
      const on = hi.vbits.has(p.capability + "." + p.signal + "." + b) || selPort;
      const r = el("rect", {x: cx, y: cy, width: 11, height: 11, rx: 2, class: "clickable",
                           fill: on ? "var(--sel)" : "#e7f5ff", stroke: "#1c7ed6"});
      r.append(el("title", {}, perBit ? p.signal + "[" + b + "]" : p.signal));
      r.addEventListener("click", (e) => { e.stopPropagation();
        select(perBit ? {kind: "vbit", cap: p.capability, signal: p.signal, bit: b} : {kind: "vport", cap: p.capability, signal: p.signal}); });
      svg.append(r);
    }
    const rowsUsed = Math.ceil(cells / per);
    portRows.push({p, x: VX + 118 + Math.min(cells, per) * 13, y: y + 6});
    y += 14 * rowsUsed + 8;
  }
  if (!ports().length) svg.append(el("text", {x: VX, y: y + 10, fill: "#868e96"}, S.ev && S.ev.trace === null && S.ev.problems.length ? "(fix the problems to trace)" : "(nothing yet)"));

  // board frame, on-board devices
  const used = new Map();
  (S.setup.use || []).forEach((u, i) => { if (u.onboard) used.set(u.onboard, i); });
  let oy = top + 40;
  const boardItems = [];
  for (const o of S.board.onboard) {
    const i = used.has(o.id) ? used.get(o.id) : null;
    const on = i !== null && hi.uses.has(i), selected = S.sel && S.sel.kind === "onboard" && S.sel.id === o.id;
    const g = el("g", {class: "clickable"});
    g.append(el("rect", {x: BX + 12, y: oy, width: OW, height: 20, rx: 3,
                        fill: i !== null ? "#d3f9d8" : "#ffffff", stroke: on || selected ? "var(--sel)" : "#868e96",
                        "stroke-width": on || selected ? 2.5 : 1}));
    const lt = el("text", {x: BX + 18, y: oy + 14, "font-size": 12, fill: i === null ? "#868e96" : "#212529"}, o.label);
    lt.append(el("title", {}, o.label + (i === null ? " — not used by this setup" : "")));
    g.append(lt);
    g.addEventListener("click", () => select({kind: "onboard", id: o.id}));
    boardItems.push(g);
    if (i !== null) S.pos.uses[i] = {x: BX + 12, y: oy + 10, xr: BX + 12 + OW};
    oy += 26;
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

  // design port -> provider links
  for (const pr of portRows) for (const prov of pr.p.providers) {
    const u = S.pos.uses[prov.attach_index];
    if (!u) continue;
    const isSel = hi.links.has(pr.p.capability + "." + pr.p.signal + "#" + prov.attach_index);
    const tx = u.x < BX + 20 ? u.x : u.x;       // left edge of the provider
    const d = "M" + pr.x + "," + pr.y + " C" + (pr.x + 60) + "," + pr.y + " " + (tx - 60) + "," + u.y + " " + tx + "," + u.y;
    svg.append(el("path", {d, fill: "none", stroke: isSel ? "var(--sel)" : "#adb5bd", "stroke-width": isSel ? 2.5 : 1,
                           "stroke-dasharray": isSel ? "" : "4 3", opacity: S.sel && !isSel ? 0.25 : 0.8}));
  }

  const W = MX + 260, H = Math.max(top + boardH, my, y) + 30;
  svg.setAttribute("width", W); svg.setAttribute("height", H); svg.setAttribute("viewBox", "0 0 " + W + " " + H);
}

// what the current selection lights up
function highlight() {
  const hi = {pins: new Set(), wires: new Set(), mpins: new Set(), uses: new Set(), vbits: new Set(), links: new Set()};
  const sel = S.sel;
  if (!sel) return hi;
  const ri = refIndex();
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
      if (!b.ref) addUse(pr.attach_index);      // through a driver: the whole part
    }
  };
  if (sel.kind === "vbit") {
    const p = ports().find((q) => q.capability === sel.cap && q.signal === sel.signal);
    if (p) addBit(p, sel.bit);
  } else if (sel.kind === "vport") {
    const p = ports().find((q) => q.capability === sel.cap && q.signal === sel.signal);
    if (p) for (const pr of p.providers) {
      hi.links.add(p.capability + "." + p.signal + "#" + pr.attach_index);
      addUse(pr.attach_index);
      for (const b of pr.bits || []) { if (b.design_bit !== null) hi.vbits.add(p.capability + "." + p.signal + "." + b.design_bit); if (b.ref && ri[b.ref]) hi.pins.add(ri[b.ref]); }
    }
  } else if (sel.kind === "use" || sel.kind === "onboard" || sel.kind === "conn") {
    let i = sel.use;
    if (sel.kind === "onboard") i = (S.setup.use || []).findIndex((u) => u.onboard === sel.id);
    if (sel.kind === "conn") i = (S.setup.use || []).findIndex((u) => u.gpio === sel.id);
    if (i !== undefined && i >= 0) {
      addUse(i);
      for (const x of portsOfUse(i)) {
        hi.links.add(x.port.capability + "." + x.port.signal + "#" + i);
        for (const b of x.bits) hi.vbits.add(x.port.capability + "." + x.port.signal + "." + b);
      }
    }
  } else if (sel.kind === "pin" || sel.kind === "wire") {
    let key = sel.kind === "pin" ? sel.conn + "." + sel.key : wiresOf(S.setup.use[sel.use])[sel.pin];
    hi.pins.add(key);
    (S.setup.use || []).forEach((u, i) => { if (u.module) for (const [p, w] of Object.entries(wiresOf(u))) if (w === key) { hi.wires.add(i + "." + p); hi.mpins.add(i + "." + p); hi.uses.add(i); } });
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
        d.append(chain(["design " + p.signal + (sel.kind === "vbit" ? "[" + sel.bit + "]" : ""), useLabel(use) + " (" + pr.peripheral + ")",
                        pr.via ? "via " + pr.via : "",
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
    d.append(h("table", {}, h("tr", {}, h("td", {}, "pinmap"), h("td", {}, pin.ref)),
                          h("tr", {}, h("td", {}, "FPGA pin"), h("td", {}, pin.pin)),
                          h("tr", {}, h("td", {}, "voltage"), h("td", {}, (c.voltage || "?") + " V"))));
    let any = false;
    uses.forEach((u, i) => { if (u.module) for (const [p, w] of Object.entries(wiresOf(u))) if (w === key) {
      any = true;
      const m = moduleDef(u.module);
      const bits = designBitsOfRef(pin.ref).map((b) => "design " + b.port.signal + "[" + b.bit + "]");
      const prov = portsOfUse(i).map((x) => "design " + x.port.signal + (x.via ? " (via " + x.via + ")" : ""));
      d.append(chain([bits.join(", ") || prov.join(", "), useLabel(u) + " pin " + p + " (" + m.pins[p] + ")", pinText(cid, k)]));
      if (!STATIC) d.append(h("button", {onclick: () => { const uu = S.setup.use[i]; if (uu.plug) { uu.wires = wiresOf(uu); delete uu.plug; } delete uu.wires[p]; S.sel = {kind: "use", use: i}; changed("disconnected " + p); }}, "Disconnect " + p));
    } });
    const gi = uses.findIndex((u) => u.gpio === cid);
    if (gi >= 0) { any = true; d.append(chain(designBitsOfRef(pin.ref).map((b) => "design " + b.port.signal + "[" + b.bit + "]").concat(["gpio " + c.label])) ); }
    if (!any) d.append(h("p", {}, "Not connected."));
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
    if (!STATIC) d.append(h("div", {class: "chain"}, "Or click a module pin in the drawing, then a header pin."));
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
  if (!probs.length) pl.append(h("li", {}, "no problems"));
  for (const p of probs) pl.append(h("li", {class: p.level}, p.level + ": " + p.message));
  $("config-text").textContent = (S.ev && S.ev.configuration_text) || "";
}

function render() { draw(); details(); tables(); }

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
  } catch (e) { status(e.message, true); }
  render();
}

async function loadBoard(id, setupId) {
  S.board = await api("/api/board/" + encodeURIComponent(id));
  fillSelect($("setup"), S.board.setups, setupId);
  fillSelect($("toolchain"), S.board.toolchains);
  fillSelect($("add-module"), S.board.modules.map((m) => m.id), null, (id) => moduleDef(id).name + " (" + moduleDef(id).peripheral + ")");
  fillSelect($("design"), S.board.designs, S.board.designs.includes("1_06_binary_counter") ? "1_06_binary_counter" : null);
  const sid = setupId || S.board.setups[0];
  if (sid) await loadSetup(sid);
  else { S.setup = {id: "", board: id, toolchain: S.board.toolchains[0], use: []}; S.ev = null; render(); }
}

async function loadSetup(id) {
  S.setup = await api("/api/setup/" + encodeURIComponent(id));
  S.sel = null; S.pending = null; S.dirty = false;
  $("setup").value = id;
  $("toolchain").value = S.setup.toolchain;
  S.ev = await api("/api/evaluate", {setup: S.setup});
  status("loaded " + id);
  render();
}

function fillSelect(sel, values, current, label) {
  sel.replaceChildren(...values.map((v) => { const o = h("option", {value: v}, label ? label(v) : v); if (v === current) o.selected = true; return o; }));
}

function wire() {
  $("board").addEventListener("change", (e) => loadBoard(e.target.value).catch((x) => status(x.message, true)));
  $("setup").addEventListener("change", (e) => loadSetup(e.target.value).catch((x) => status(x.message, true)));
  $("toolchain").addEventListener("change", (e) => { S.setup.toolchain = e.target.value; changed("toolchain " + e.target.value); });
  $("add").addEventListener("click", () => {
    const id = $("add-module").value;
    S.setup.use.push({module: id, wires: {}});
    S.sel = {kind: "use", use: S.setup.use.length - 1};
    changed("added " + moduleDef(id).name + " — wire its pins");
  });
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
    try { const r = await api("/api/save", {setup: S.setup}); S.dirty = false; status("saved " + r.setup + " and " + r.configuration); }
    catch (e) { status(e.message, true); }
  });
  $("project").addEventListener("click", async () => {
    try {
      if (S.dirty) throw new Error("save the setup first");
      const design = $("design").value;
      status("generating the project for " + design + "…");
      const r = await api("/api/project", {setup_id: S.setup.id, design});
      const link = h("a", {href: "/api/project/" + encodeURIComponent(S.setup.id) + "/" + encodeURIComponent(design) + ".zip"}, "download zip");
      $("status").replaceChildren((r.ok ? "project written to " : "project (with errors) in ") + r.output + " (" + r.files.length + " files) — ", link);
    } catch (e) { status(e.message, true); }
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") { S.pending = null; S.sel = null; status(""); render(); } });
}

async function main() {
  if (STATIC) {
    document.body.classList.add("readonly");
    S.board = STATIC.board; S.setup = STATIC.setup; S.ev = STATIC.evaluation;
    fillSelect($("board"), [S.board.board]); fillSelect($("setup"), [S.setup.id]);
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
    S.setup.id = "selftest_rig"; delete S.setup.notes;       // a fresh rig: no design-wiring profile
    await changed("copy"); await settle();
    const n = S.setup.use.length;
    S.setup.use.push({module: "tm1638_led_key", wires: {}}); await changed("add"); await settle();
    ok("added module is listed", S.setup.use.length === n + 1);
    ok("unwired module reports its required signals", errors().some((m) => m.includes("required signal")));
    const i = S.setup.use.length - 1;
    const free = S.board.connectors.flatMap((c) => Object.keys(c.pins).map((k) => [c.id, k]))
      .filter(([c, k]) => !connectionRows().some((r) => r.header === conn(c).label + " pin " + k) &&
                        !S.setup.use.some((u) => u.gpio === c));
    const target = free.slice(-3);
    for (const [p, [c, k]] of ["STB", "CLK", "DIO"].map((p, j) => [p, target[j]])) { modulePinClicked(i, p); pinClicked(c, k); await settle(); }
    ok("wired by clicking module pin then header pin", Object.keys(S.setup.use[i].wires).length === 3);
    ok("the new board adds design LEDs, traced to it", errors().length === 0 &&
       portsOfUse(i).some((x) => x.port.capability === "leds" && x.bits.length === 8));
    const [c0, k0] = target[0];
    pinClicked(c0, k0); await settle();
    ok("selecting a header pin shows its wire", $("details").textContent.includes("pin STB"));
    delete S.setup.use[i].wires["STB"]; await changed("disconnect"); await settle();
    ok("disconnecting brings back the required-signal error", errors().some((m) => m.includes("required signal 'stb'")));
    S.setup.use.splice(i, 1); await changed("remove"); await settle();
    ok("removing the module restores the rig", S.setup.use.length === n);
    const ob = S.board.onboard.find((o) => S.setup.use.some((u) => u.onboard === o.id && o.id !== "clock"));
    const oi = S.setup.use.findIndex((u) => u.onboard === ob.id);
    S.setup.use.splice(oi, 1); await changed("unuse"); await settle();
    ok("an on-board device can be dropped", !S.setup.use.some((u) => u.onboard === ob.id));
    const r = await api("/api/save", {setup: S.setup});
    ok("saved", r.configuration.endsWith("selftest_rig.yml"));
    S.dirty = false;
    const pr = await api("/api/project", {setup_id: "selftest_rig", design: "1_06_binary_counter"});
    ok("project generated with a top", pr.ok && pr.files.includes("top.sv"));
  } catch (e) { log.push("FAIL exception: " + e.message); }
  document.body.append(h("pre", {id: "selftest"}, log.join("\n")));
}

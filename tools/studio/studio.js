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
  for (const [k, v] of Object.entries(attrs || {})) if (v !== null && v !== undefined && v !== false) e.setAttribute(k, v);
  if (text !== undefined) e.textContent = text;
  return e;
}
function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;          // an attribute left out
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
// an on-board part is attached one way, or as one of its variants (the panels an LCD connector takes ...)
function variantsOf(o) { return (o && o.variants) || (o ? [{id: null, label: o.label, attach: o.attach, pins: o.pins}] : []); }
function variantOf(o, use) { const vs = variantsOf(o); return vs.find((v) => v.id === ((use && use.variant) || null)) || vs[0]; }
function onboardPins(o, use) { const v = variantOf(o, use); return v ? v.pins : {}; }
// what a module pin must be when it is left unwired (the module's `unwired:`), "tie to GND" ...
function unwiredTie(m, p) {
  const u = ((m && m.unwired) || {})[p];
  return u ? "tie to " + ({ground: "GND", power: "VCC"}[u.tie] || u.tie) : "";
}
function passive(sig) { return sig === "power" || sig === "ground"; }

// wires of a module use, plug expanded ({module pin: "conn.key"})
function wiresOf(use) {
  if (use.plug) {
    const m = moduleDef(use.module), c = conn(use.plug.connector);
    // row "all": a module as big as the connector, over its rows in order; reversed: turned round
    let row = use.plug.row === "all" ? c.rows.flat() : (c.rows[(use.plug.row || 1) - 1] || []);
    if (use.plug.reversed) row = row.slice().reverse();
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
  if (use.onboard) { const o = onboardDef(use.onboard); return ((o || {}).label || use.onboard) + (use.variant && o && variantsOf(o).length > 1 ? ": " + variantOf(o, use).label : ""); }
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
function designParams() { return (S.ev && S.ev.trace && S.ev.trace.parameters) || {}; }
// "w_red = 4": a port whose width is a design_top parameter the rig sets
function widthText(p) { return p.width_parameter ? p.width_parameter + " = " + (p.width || 0) : ""; }
// a capability's summary of what this rig gives design_top, from its template
// (config/capabilities/<id>.yml `summary`: "{screen_width}×{screen_height}, rgb{w_red + w_green + w_blue} ...")
function capDef(cid) { return (S.board.capabilities || []).find((c) => c.id === cid) || {}; }
function capSummary(cid) {
  const q = designParams();
  return (capDef(cid).summary || "").replace(/\{([^}]+)\}/g, (_, e) => String(e.split("+").reduce((s, k) => s + (Number(q[k.trim()]) || 0), 0)));
}
// the requirement line a design writes for a sized capability (`screen >= 640x480@444`)
function sizeRequirement(cid) {
  const c = capDef(cid), q = designParams();
  if (!c.size) return "";
  const own = c.design_parameters || [];
  return "// requires: " + cid + " >= " + own.slice(0, 2).map((k) => q[k] || 0).join("x") +
         (c.depth ? "@" + c.depth.map((k) => q[k] || 0).join("") : "");
}
function providersText(p) {
  return p.providers.map((pr) => { const a = attachOf(pr.attach_index) || {}, prm = Object.entries(a.params || {});
    return useLabel(S.setup.use[pr.attach_index] || {}) + " (" + pr.peripheral + (prm.length ? ": " + prm.map(([k, v]) => k + " = " + v).join(", ") : "") + ")"; }).join("; ");
}

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
  if (ed.relation === "or") return "the " + ed.via + " driver ORs " + bitRange(ed.design_port, ed.bits) + " onto this one pin";
  return n > 1 ? "no single bit: the " + ed.via + " driver carries " + bitRange(ed.design_port, ed.bits) +
                 " over this one pin (shifted serially / time-multiplexed, or as timing such as a sync)"
               : "the pin carries " + bitRange(ed.design_port, ed.bits || []) + " through the " + ed.via + " driver";
}

// how a driver relates a pin to design bits (tools/trace.py _fit), in words
function relationText(ed, p) {
  if (ed.bit === null) return wholePortText(ed, p);
  const pin = pinLabel(ed) + " ↔ " + ed.design_port + "[" + ed.bit + "]";
  if (ed.relation === "msb") {
    const w = p.width || 0, npins = ((attachOf(ed.use) || {}).pins || {})[ed.signal] || [];
    return "the driver keeps the top " + npins.length + " of " + w + " bits (" + pin + "); " +
           bitRange(ed.design_port, [...Array(Math.max(w - npins.length, 0)).keys()]) + " reach no pin";
  }
  return "bit for bit (" + pin + ")";
}

// one design bit <-> pin edge, spelled out
function edgeText(ed) {
  const ri = refIndex(), hp = ri[ed.ref], c = hp && conn(hp.split(".")[0]);
  const where = hp ? c.label + " pin " + hp.split(".")[1] + " = " + ed.ref : ed.ref;
  const use = S.setup.use[ed.use];
  const p = ports().find((q) => q.design_port === ed.design_port);
  return "design " + (ed.bit === null ? bitRange(ed.design_port, ed.bits || []) + (ed.relation === "or" ? " (ORed onto this one pin)" : " (together, over this one pin)")
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
    // `own`: the driver's port names (tm1638_board_controller's sio_data, i2s_audio_out's data_in)
    const pl = pinLabel(ed), dl = designLabel(ed);
    add(d.pin, pinKey(ed), pl, {ref: ed.ref, own: ed.driver_port ? ed.driver_port + pl.slice(ed.signal.length) : pl});
    add(d.design, designKey(ed), dl, {ed, own: ed.driver_design_port ? ed.driver_design_port + (dl.match(/\[[^\]]*\]$/) || [""])[0] : dl});
    d.edges.push(n);
  });
  return [...out.values()];
}
function driverOf(sel) { return drivers().find((d) => d.use === sel.use && d.via === sel.via); }

// how a driver box names its connections: "connected" (what it connects to: the
// design's ports and the part's pins) or "own" (the driver module's port names);
// one choice per driver, remembered in this browser
function driverNamings() {
  if (!S.driverNames) { try { S.driverNames = JSON.parse(localStorage.getItem("unifpga.driverNames") || "{}"); } catch (e) { S.driverNames = {}; } }
  return S.driverNames;
}
function driverNaming(d) { return driverNamings()[d.via] || driverNamings()["*"] || "connected"; }
function setDriverNaming(via, mode) {
  const m = driverNamings();
  if (via === "*") { for (const k of Object.keys(m)) delete m[k]; }
  m[via] = mode;
  try { localStorage.setItem("unifpga.driverNames", JSON.stringify(m)); } catch (e) { /* a private window: this page only */ }
  render();
}

// why a part does not reach the design (server evaluate `parts`): {connected, reasons: [[kind, text]]}
function partStatus(i) { return ((S.ev && S.ev.parts) || []).find((x) => x.use === i); }
function partText(i) { const x = partStatus(i); return x ? x.reasons.map((r) => r[1]).join(" ") : ""; }
function partShort(i) {
  const x = partStatus(i);
  if (!x) return "";
  const kinds = {untraced: "no profile entry / not placed", exclusive: "capability already provided", unwired: "not wired", nothing: "nothing design_top uses"};
  const k = x.reasons[0][0];
  return (x.connected ? "partly in the design: " : "not in the design: ") + (k === "untraced" && !/profile/.test(x.reasons[0][1]) ? "cannot be placed" : kinds[k] || k);
}
// the peripheral a use attaches, and the capabilities it provides
function usePeripheral(u) {
  return u.module ? (moduleDef(u.module) || {}).peripheral : u.onboard ? ((variantOf(onboardDef(u.onboard), u) || {}).attach || {}).peripheral
       : u.gpio ? gpioPeripheral() : (u.raw || {}).peripheral;
}
// the peripheral a `gpio: <connector>` use attaches (the gpio capability's passthrough)
function gpioPeripheral() { return ((S.board.capabilities || []).find((c) => c.passthrough) || {}).passthrough; }
function providedCaps(pid) { return ((S.board.peripherals[pid] || {}).provides || []).map((p) => p.capability); }
function capAggregation(cid) { return ((S.board.capabilities || []).find((c) => c.id === cid) || {}).aggregation; }

// FPGA pins more than one part reaches: ref -> [edge per part]
function sharedRefs() {
  const m = new Map();
  for (const e of traceEdges()) { if (!m.has(e.ref)) m.set(e.ref, new Map()); if (!m.get(e.ref).has(e.use)) m.get(e.ref).set(e.use, e); }
  return new Map([...m].filter(([, u]) => u.size > 1).map(([r, u]) => [r, [...u.values()]]));
}
function sharedRefText(ref) {
  const es = sharedRefs().get(ref);
  return es ? "shared: " + es.map((e) => useLabel(S.setup.use[e.use] || {}) +
                                  (e.via ? " (its " + pinLabel(e) + ", through the " + e.via + " driver)" : " (the design's " + designLabel(e) + ", direct)")).join(" and ") +
              " — the generated top connects all of them, so only one may drive this pin at a time" : "";
}
// design bits more than one part provides: bit -> [provider labels]; inputs are ORed, outputs drive every part
function sharedBits(p) {
  const m = new Map();
  for (const pr of p.providers) for (const b of pr.bits || []) if (b.design_bit !== null) {
    if (!m.has(b.design_bit)) m.set(b.design_bit, []);
    m.get(b.design_bit).push(useLabel(S.setup.use[pr.attach_index] || {}) + " bit " + b.provider_bit);
  }
  return new Map([...m].filter(([, l]) => l.length > 1));
}
function sharedBitText(p, bit) {
  const l = sharedBits(p).get(bit);
  if (!l) return "";
  return p.direction === "hw_to_user" ? portName(p) + "[" + bit + "] = " + l.join(" OR ")
                                      : portName(p) + "[" + bit + "] drives all of: " + l.join(", ");
}

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
  const SHARED = sharedRefs();
  // design bits whose pin several parts reach: "port.bit" -> why
  const CONFLICT = new Map();
  for (const e of traceEdges()) if (SHARED.has(e.ref)) for (const b of e.bits || []) CONFLICT.set(e.design_port + "." + b, "is " + sharedRefText(e.ref));
  const VX = 12, top = 16, OW = 290;
  let BX = 330;

  // virtual device
  let y = top + 24;
  svg.append(el("text", {x: VX, y: top + 8, "font-weight": "bold", "font-size": 14}, "Virtual device (design_top)"));
  svg.append(el("text", {x: VX + 190, y: top + 8, "font-size": 9, fill: "#7048e8"}, "w_… = a width this rig gives the design"));
  const portRows = [];
  const captioned = new Set();
  for (const p of devicePorts()) {
    // a capability whose ports are sized by several parameters (its `summary`) gets a line naming the variant
    if (capDef(p.capability).summary && p.providers.length && !captioned.has(p.capability)) {
      captioned.add(p.capability);
      // two short lines, cut to the virtual device's column (the full text is the tooltip)
      const fit = (s) => s.length > 52 ? s.slice(0, 51) + "…" : s;
      const cap = el("text", {x: VX, y: y + 9, "font-size": 10, fill: "#7048e8", class: "clickable"});
      cap.append(el("tspan", {x: VX}, fit(p.capability.replace(/_/g, " ") + " " + capSummary(p.capability) + " (parameters)")));
      cap.append(el("tspan", {x: VX, dy: 12}, fit("set by " + p.providers.map((pr) => useLabel(S.setup.use[pr.attach_index] || {})).join(", "))));
      const req = sizeRequirement(p.capability);
      cap.append(el("title", {}, "design_top is parameterized: " + (capDef(p.capability).design_parameters || []).join(" / ") + " come from the rig. " +
                                 "This rig gives " + capSummary(p.capability) + "; other rigs give others, and a design reads the parameters" +
                                 (req ? " (and states its minimum with a line like `" + req + "`)" : "") + "."));
      target(cap, {kind: "vport", cap: p.capability, signal: p.signal});
      svg.append(cap);
      y += 28;
    }
    let width = p.width || 0;
    for (const pr of p.providers) for (const b of pr.bits || []) if (b.design_bit !== null) width = Math.max(width, b.design_bit + 1);
    const perBit = p.providers.some((pr) => pr.bits);
    const none = p.none || !p.providers.length;
    const g = el("g", {class: "clickable"});
    const selPort = (S.sel && S.sel.kind === "vport" && S.sel.signal === p.signal && S.sel.cap === p.capability) ||
                    hi.vports.has(p.capability + "." + p.signal);
    g.append(el("text", {x: VX, y: y + 11, "font-size": 12, fill: selPort ? "var(--sel)" : none ? "#adb5bd" : "#212529"},
                portName(p) + (width > 1 ? "[" + (width - 1) + ":0]" : "")));
    const sub = el("text", {x: VX, y: y + 22, "font-size": 9, fill: "#868e96"});
    if (p.width_parameter) sub.append(el("tspan", {fill: "#7048e8"}, widthText(p) + " · "));
    sub.append(el("tspan", {}, p.capability + "." + p.signal));
    g.append(sub);
    g.append(el("title", {}, "design_top " + portName(p) + (p.width_parameter ? "[" + p.width_parameter + " - 1 : 0]" : width > 1 ? "[" + (width - 1) + ":0]" : "") +
                             (p.width_parameter ? " — " + widthText(p) + " on this rig (a design_top parameter; other rigs give other widths)" : "") +
                             " — capability " + p.capability + "." + p.signal + " (" + (p.direction || "") + ")" + (none ? " — nothing in the rig provides it" : "")));
    target(g.firstChild, {kind: "vport", cap: p.capability, signal: p.signal});
    svg.append(g);
    const cells = Math.max(width, 1), per = 14;
    for (let b = 0; b < cells; b++) {
      const cx = VX + 118 + (b % per) * 13, cy = y + Math.floor(b / per) * 14;
      const on = hi.vbits.has(p.capability + "." + p.signal + "." + b) || selPort;
      const r = el("rect", {x: cx, y: cy, width: 11, height: 11, rx: 2, class: "clickable",
                           fill: on ? "var(--sel)" : none ? "#f8f9fa" : "#e7f5ff", stroke: none ? "#ced4da" : "#1c7ed6"});
      r.append(el("title", {}, portName(p) + (width > 1 ? "[" + b + "]" : "") + "  (" + p.capability + "." + p.signal + ")" +
                               (sharedBitText(p, b) ? " — " + sharedBitText(p, b) : "")));
      S.pos.cells[portName(p) + "." + b] = {x: cx + 11, y: cy + 5.5};
      target(r, width > 1 || perBit ? {kind: "vbit", cap: p.capability, signal: p.signal, bit: b} : {kind: "vport", cap: p.capability, signal: p.signal});
      svg.append(r);
      if (sharedBitText(p, b))
        svg.append(el("path", {d: "M" + (cx + 5) + "," + cy + " h6 v6 z", fill: "#f76707", "pointer-events": "none"}));
      if (CONFLICT.has(portName(p) + "." + b)) {
        r.setAttribute("stroke", "#f76707"); r.setAttribute("stroke-width", "2");
        r.querySelector("title").textContent += " — conflicted: its pin " + CONFLICT.get(portName(p) + "." + b);
      }
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
    const obpins = i === null ? [] : Object.entries(onboardPins(o, S.setup.use[i])).flatMap(([s, ps]) => ps.map((pp) => Object.assign({s}, pp)));
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
      if (SHARED.has(pp.ref)) g.append(el("circle", {cx: px, cy: py, r: 6.5, fill: "none", stroke: "#f76707", "stroke-width": 2, "pointer-events": "none"}));
    });
    // the label cut to the box (about 6.3 px a character at 12 px), the whole of it on hover
    const full = o.label + (o.device ? "  — no model yet" : "") + (i !== null && variantsOf(o).length > 1 ? ": " + variantOf(o, S.setup.use[i]).label : variantsOf(o).length > 1 ? " (" + variantsOf(o).length + " ways)" : "") +
                 (dropped ? "  — not in the design (profile)" : i !== null && partStatus(i) ? "  — " + partShort(i) : "");
    const room = Math.floor((OW - 14) / 6.3), tail = full.slice(o.label.length);
    const name = o.label.length + tail.length > room ? o.label.slice(0, Math.max(8, room - tail.length - 1)) + "…" : o.label;
    const lt = el("text", {x: BX + 18, y: oy + 14, "font-size": 12, fill: i === null || dropped ? "#868e96" : "#212529",
                           "font-style": o.device ? "italic" : null},
                  (name + tail).length > room ? (name + tail).slice(0, room - 1) + "…" : name + tail);
    lt.append(el("title", {}, full + (i === null ? " — not used by this setup" : dropped ? " — " + dropText(i) : "")));
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
  const gpioPins = new Set();                 // `pins:` hands only these of a connector to the design
  (S.setup.use || []).forEach((u, i) => { if (u.gpio) { gpioConn.set(u.gpio, i); for (const k of u.pins || []) gpioPins.add(u.gpio + "." + k); } });
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
                   c.label + (gi !== null ? "  — design gpio" + (S.setup.use[gi].pins ? " (" + S.setup.use[gi].pins.length + " pins)" : "") : ""));
    if (c.note) lbl.append(el("title", {}, c.label + ": " + c.note));   // hover: how far the model is verified
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
      const toGpio = gpioPins.has(c.id + "." + key);
      const circ = el("circle", {cx: px, cy: py, r: 6, fill, stroke: isSel ? "var(--sel)" : toGpio ? "#2b8a3e" : "#495057",
                                 "stroke-width": isSel || toGpio ? 2 : 1, class: pin ? "clickable" : ""});
      circ.append(el("title", {}, c.label + " pin " + key + (pin ? ": " + pin.ref + " = " + pin.pin : pw ? ": " + pw : "")));
      if (pin) target(circ, {kind: "pin", conn: c.id, key});
      g.append(circ);
      if (pin && SHARED.has(pin.ref)) {
        const ring = el("circle", {cx: px, cy: py, r: 8.5, fill: "none", stroke: "#f76707", "stroke-width": 2, "pointer-events": "none"});
        circ.querySelector("title").textContent += " — " + sharedRefText(pin.ref);
        g.append(ring);
      }
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
  const frame = el("rect", {x: BX, y: top, width: boardW, height: boardH, rx: 8, fill: "#e7f5ff",
                            stroke: S.sel && S.sel.kind === "board" ? "var(--sel)" : "#1c7ed6", "stroke-width": 2});
  target(frame, {kind: "board"});
  svg.append(frame);
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
    const st = partStatus(i), gap = st ? 14 : 0;
    const hgt = 28 + gap + PITCH * Math.max(pins.length, 1);
    const on = hi.uses.has(i) || (S.sel && S.sel.kind === "use" && S.sel.use === i);
    const g = el("g");
    const box = el("rect", {x: MX, y: my, width: 230, height: hgt, rx: 5, fill: st && !st.connected ? "#f8f9fa" : "#fff", stroke: col,
                           "stroke-width": on ? 3.5 : 2, "stroke-dasharray": st && !st.connected ? "5 3" : "", class: "clickable"});
    target(box, {kind: "use", use: i});
    g.append(box);
    const title = el("text", {x: MX + 8, y: my + 17, "font-weight": "bold", "font-size": 12, fill: col, class: "clickable"}, useLabel(use));
    target(title, {kind: "use", use: i});
    g.append(title);
    S.pos.uses[i] = {x: MX, y: my + 12, xr: MX + 230};
    if (st) {
      const note = el("text", {x: MX + 8, y: my + 31, "font-size": 10, fill: "#c92a2a", class: "clickable"}, partShort(i));
      note.append(el("title", {}, partText(i)));
      target(note, {kind: "use", use: i});
      g.append(note);
    }
    pins.forEach((p, k) => {
      const py = my + 28 + gap + k * PITCH + PITCH / 2;
      S.pos.mpins[i + "." + p] = {x: MX, y: py};
      const pend = S.pending && S.pending.use === i && S.pending.pin === p;
      const isSel = hi.mpins.has(i + "." + p);
      const t = el("text", {x: MX + 16, y: py + 4, "font-size": 11, class: "clickable",
                            fill: pend ? "#f76707" : isSel ? "var(--sel)" : "#343a40",
                            "font-weight": pend || isSel ? "bold" : "normal"},
                   p + "  (" + m.pins[p] + ")" + (w[p] ? "  → " + w[p] : unwiredTie(m, p) ? "  — " + unwiredTie(m, p) + " on the module" : "  — not wired"));
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
    // the box says which names it shows: a driver whose pin ports are named like the
    // part's pins (i2s_audio_out's mclk / bclk ...) looks the same on that side either way
    const sub = (driverNaming(d) === "own" ? "own port names · " : "") + "driver in the FPGA for " + useLabel(use);
    g.append(el("text", {x: DX + 8, y: d.y + 26, "font-size": 9, fill: "#868e96"}, sub.length > 40 ? sub.slice(0, 39) + "…" : sub));
    g.append(el("title", {}, d.via + ": logic inside the FPGA between design_top and " + useLabel(use) + "'s pins"));
    for (const c of g.children) if (c.tagName !== "title") target(c, {kind: "driver", use: d.use, via: d.via});
    svg.append(g);
    for (const [m, x, anchor, side] of [[d.design, DX + 5, "start", "design"], [d.pin, DX + DW - 5, "end", "pin"]])
      for (const an of m.values()) {
        const on = lit(an.edges);
        // names: what the driver connects to (the default), or its own ports; the other on hover
        const own = driverNaming(d) === "own", t = el("text", {x, y: an.y + 3, "font-size": 9, "text-anchor": anchor,
                                                               fill: on ? "var(--sel)" : "#495057"}, own ? an.own : an.label);
        t.append(el("title", {}, own ? d.via + " port " + an.own + " — connected to " + (side === "design" ? "design " : "") + an.label
                                     : (side === "design" ? "design " : "") + an.label + " — " + d.via + " port " + an.own));
        svg.append(t);
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
  if (SHARED.size) legend.push(["#f76707", "", "orange ring / outlined bit: an FPGA pin several parts reach, and the design bits on it (conflicted: only one may drive it)"]);
  if (ports().some((p) => sharedBits(p).size)) legend.push(["#f76707", "", "orange corner: a design bit several parts feed (inputs ORed, outputs drive all)"]);
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
  if (sel.kind === "board") return "board: " + S.board.board;
  return sel.kind;
}

// what is under the pointer, topmost first; a filled box (a part, a driver)
// hides what lies beneath it, so nothing under it is a candidate
function candidatesAt(x, y) {
  const seen = new Set(), out = [];
  for (const e of document.elementsFromPoint(x, y)) {
    const t = e.closest && e.closest("[data-sel]");
    if (t) {
      const key = t.getAttribute("data-sel");
      if (!seen.has(key)) { seen.add(key); out.push(JSON.parse(key)); }
    }
    const fill = e.tagName === "rect" ? (e.getAttribute("fill") || "") : "none";
    if (fill && fill !== "none" && fill !== "transparent") break;
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
  if (isDoubleClick(e)) return onSvgDoubleClick(e);
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

// ---------------------------------------------------------------- context menu
// A right click selects what is under the pointer (as a click would) and offers
// what can be done with it: its Verilog, and for a driver how its box names
// its connections.
function menuItems(sel) {
  const items = [];
  const drv = sel.kind === "driver" || sel.kind === "dseg" ? sel : null;
  if (drv) {
    const d = {via: drv.via}, mode = driverNaming(d);
    items.push({head: "Names in the " + drv.via + " box"});
    items.push({label: "what it connects to (design ports, part pins)", checked: mode === "connected",
                run: () => setDriverNaming(drv.via, "connected")});
    items.push({label: "its own port names", checked: mode === "own", run: () => setDriverNaming(drv.via, "own")});
    items.push({label: mode === "own" ? "…what it connects to, in every driver" : "…its own port names, in every driver",
                run: () => setDriverNaming("*", mode === "own" ? "connected" : "own")});
    items.push({sep: true});
  }
  items.push({label: "Show Verilog source", run: () => openVerilog(sel).catch((x) => status(x.message, true))});
  return items;
}

function openMenu(x, y, sel) {
  closePicker();
  const items = menuItems(sel);
  const box = h("div", {id: "picker", class: "menu", role: "menu"},
    h("div", {class: "picker-head"}, selLabel(sel)),
    ...items.map((it) => it.sep ? h("div", {class: "menu-sep"})
      : it.head ? h("div", {class: "picker-tip"}, it.head)
      : h("button", {class: "picker-item", role: "menuitem",
                     onclick: () => { closePicker(); it.run(); }}, (it.checked === undefined ? "" : it.checked ? "● " : "○ ") + it.label)));
  box.style.left = Math.min(x + 4, window.innerWidth - 420) + "px";
  box.style.top = Math.min(y + 4, window.innerHeight - 40 - 30 * items.length) + "px";
  document.body.append(box);
}

function onSvgContextMenu(e) {
  e.preventDefault();
  const all = candidatesAt(e.clientX, e.clientY);
  if (!all.length) { closePicker(); return; }
  // a driver under the pointer is what a right click on its box means
  const pick = all.find((s) => s.kind === "driver") || all.find((s) => POINTS.has(s.kind)) ||
               all.find((s) => CONNECTIONS.has(s.kind)) || all[0];
  select(pick);
  openMenu(e.clientX, e.clientY, pick);
}

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
  svg.addEventListener("contextmenu", onSvgContextMenu);
  document.addEventListener("pointerdown", (e) => { const p = $("picker"); if (p && !p.contains(e.target)) closePicker(); });
  $("zoom-in").addEventListener("click", () => zoomCenter(1 / 1.25));
  $("zoom-out").addEventListener("click", () => zoomCenter(1.25));
  $("zoom-fit").addEventListener("click", () => { S.view = null; applyView(); });
}

// A double click is two clicks close in time and place, told apart here
// rather than by the browser's dblclick: the first click selects and redraws
// the drawing, so the second lands on a new element and browsers then send no
// dblclick. On the background it fits the drawing; on anything else it opens
// the Verilog that defines it.
const DOUBLE_MS = 450, DOUBLE_PX = 6;
function isDoubleClick(e) {
  const now = Date.now(), last = S.lastClick;
  const mods = ["metaKey", "ctrlKey", "altKey", "shiftKey"].map((k) => !!e[k]).join();
  S.lastClick = {t: now, x: e.clientX, y: e.clientY, mods};
  if (last && last.mods === mods && now - last.t <= DOUBLE_MS &&
      Math.abs(last.x - e.clientX) <= DOUBLE_PX && Math.abs(last.y - e.clientY) <= DOUBLE_PX) {
    S.lastClick = null;
    return true;
  }
  return false;
}

function onSvgDoubleClick(e) {
  closePicker();
  const all = candidatesAt(e.clientX, e.clientY);
  if (!all.length) { S.view = null; applyView(); return; }
  const points = all.filter((s) => POINTS.has(s.kind)), conns = all.filter((s) => CONNECTIONS.has(s.kind));
  const parts = all.filter((s) => !POINTS.has(s.kind) && !CONNECTIONS.has(s.kind));
  const pick = e[MODS.connsKey] ? conns[0] || points[0] || parts[0] : points[0] || conns[0] || parts[0];
  openVerilog(pick).catch((x) => status(x.message, true));
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

// ---------------------------------------------------------------- Designs: every design against every configuration
async function loadDesigns() {
  if (STATIC || S.dt || S.dtLoading) return;
  S.dtLoading = true;
  try { S.dt = await api("/api/designs"); }
  catch (e) { $("d-list").replaceChildren(h("p", {class: "note"}, e.message)); return; }
  finally { S.dtLoading = false; }
  const boards = [...new Map(S.dt.configurations.map((c) => [c.board, c.board_name])).entries()].sort((a, b) => a[1].localeCompare(b[1]));
  $("d-board").replaceChildren(h("option", {value: ""}, "all boards (" + boards.length + ")"),
                               ...boards.map(([id, name]) => h("option", {value: id}, name + " (" + id + ")")));
  renderDesigns();
}

// designs are grouped by the number their name starts with (1_02_mux and 01_... in 1)
function designGroup(id) { const m = id.match(/^(\d+)_/); return m ? String(parseInt(m[1], 10)) : "other"; }
function requireText(d) { return d.requires.length ? d.requires.join(" · ") : "no requirements declared"; }
function boardConfigs(board) { return S.dt.configurations.map((c, k) => [c, k]).filter(([c]) => !board || c.board === board).map(([, k]) => k); }

function renderDesigns() {
  if (!S.dt) return;
  const q = $("d-search").value.trim().toLowerCase(), board = $("d-board").value, only = $("d-fitonly").checked && board;
  const idx = new Set(boardConfigs(board));
  const rows = S.dt.designs
    .map((d) => ({d, n: d.fits.filter((k) => idx.has(k)).length}))
    .filter(({d, n}) => (!q || (d.id + " " + d.requires.join(" ")).toLowerCase().includes(q)) && (!only || n))
    .sort((a, b) => a.d.id.localeCompare(b.d.id, undefined, {numeric: true}));
  $("d-count").textContent = rows.length + " of " + S.dt.designs.length + " designs · " + idx.size + " configuration" + (idx.size === 1 ? "" : "s") +
                             (board ? " on " + board : " on " + new Set(S.dt.configurations.map((c) => c.board)).size + " boards");
  const out = [];
  let group = null;
  for (const {d, n} of rows) {
    const g = designGroup(d.id);
    if (g !== group) { group = g; out.push(h("div", {class: "d-group"}, g === "other" ? "other designs" : "designs " + g + "_…")); }
    const bar = h("div", {class: "fitbar", title: "fits " + n + " of " + idx.size + " configurations"}, h("span", {}), h("b", {}, n + " / " + idx.size));
    bar.firstChild.style.width = (idx.size ? 100 * n / idx.size : 0) + "%";
    out.push(h("div", {class: "d-row" + (d.id === S.dsel ? " cur" : "") + (n ? "" : " nofit"), "data-design": d.id, onclick: () => showDesign(d.id)},
               h("span", {}, d.id), h("span", {class: "req"}, requireText(d)), bar));
  }
  $("d-list").replaceChildren(...(out.length ? out : [h("p", {class: "muted"}, "No design matches.")]));
  if (S.dsel) showDesign(S.dsel, true);
}

function showDesign(id, keep) {
  const d = S.dt.designs.find((x) => x.id === id);
  if (!d) return;
  S.dsel = id;
  for (const r of document.querySelectorAll(".d-row")) r.classList.toggle("cur", r.dataset.design === id);
  const board = $("d-board").value, cfgs = S.dt.configurations;
  const byBoard = new Map();
  for (const k of boardConfigs(board)) { const c = cfgs[k]; if (!byBoard.has(c.board)) byBoard.set(c.board, []); byBoard.get(c.board).push(k); }
  const fits = new Set(d.fits);
  const sections = [...byBoard.entries()]
    .sort((a, b) => b[1].filter((k) => fits.has(k)).length - a[1].filter((k) => fits.has(k)).length || cfgs[a[1][0]].board_name.localeCompare(cfgs[b[1][0]].board_name))
    .map(([b, ks]) => h("div", {},
      h("div", {class: "board"}, cfgs[ks[0]].board_name + " (" + b + ") — fits " + ks.filter((k) => fits.has(k)).length + " of " + ks.length +
                                 (cfgs[ks[0]].layout ? "" : "  · no rig drawing for this board yet")),
      ...ks.map((k) => {
        const c = cfgs[k], ok = fits.has(k);
        return h("div", {class: "cfg" + (ok ? "" : " no")}, ok ? "✓" : "✗", h("span", {}, c.id + " · " + (c.toolchain || "?")),
                 ok && c.setup ? h("button", {onclick: () => openRig(c, id).catch((x) => status(x.message, true))}, "Open rig") : "",
                 ok ? "" : h("span", {class: "why"}, (d.unmet[k] || []).join("; ")));
      })));
  const dd = $("d-detail"), shown = dd.querySelector("#d-code");
  dd.replaceChildren(
    h("h3", {}, id),
    d.requires.length ? h("div", {}, h("div", {class: "muted"}, "// requires:"), ...d.requires.map((r) => h("div", {class: "chain"}, r)))
                      : h("p", {class: "muted"}, "No // requires: block: it runs on any configuration."),
    h("p", {}, "Fits " + d.fits.length + " of " + cfgs.length + " configurations in the repository."),
    h("button", {onclick: () => showDesignSource(id)}, "View design_top.sv"),
    keep && shown ? shown : h("div", {id: "d-code", hidden: true}),
    ...sections);
}

async function showDesignSource(id) {
  const src = await api("/api/module", {name: "design_top", design: id});
  const box = $("d-code");
  box.hidden = false; box.classList.add("code");
  renderCode(box, src.text);
}

// the rig editor on a configuration that has a setup, with this design chosen
async function openRig(c, design) {
  showTab("rig");
  if (!S.board || S.board.board !== c.board) { $("board").value = c.board; await loadBoard(c.board, c.id); }
  else if (!S.setup || S.setup.id !== c.id) { $("setup").value = c.id; await loadSetup(c.id); }
  S.design = design; designChoices();
  status("opened " + c.id + " with " + design + " chosen (Configuration tab: generate its project)");
}

// ---------------------------------------------------------------- side panel: splitter, tabs
function wireSplitter() {
  const sp = $("splitter"), side = $("side");
  const set = (w, keep) => {
    const max = Math.max(260, document.querySelector("main").clientWidth * 0.75);
    w = Math.round(Math.max(240, Math.min(max, w)));
    side.style.flexBasis = w + "px";
    if (keep) try { localStorage.setItem("unifpga.studio.side", String(w)); } catch (e) { /* storage unavailable */ }
    return w;
  };
  try { const w = Number(localStorage.getItem("unifpga.studio.side")); if (w) set(w); } catch (e) { /* storage unavailable */ }
  sp.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    try { sp.setPointerCapture(e.pointerId); } catch (x) { /* no live pointer (a synthetic event) */ }
    sp.classList.add("dragging");
    const x0 = e.clientX, w0 = side.getBoundingClientRect().width;
    const move = (ev) => set(w0 - (ev.clientX - x0));
    const up = (ev) => { set(w0 - (ev.clientX - x0), true); sp.classList.remove("dragging");
                         sp.removeEventListener("pointermove", move); sp.removeEventListener("pointerup", up); };
    sp.addEventListener("pointermove", move); sp.addEventListener("pointerup", up);
  });
  sp.addEventListener("keydown", (e) => {
    const w = side.getBoundingClientRect().width;
    if (e.key === "ArrowLeft") { e.preventDefault(); set(w + 24, true); }
    if (e.key === "ArrowRight") { e.preventDefault(); set(w - 24, true); }
  });
}

function showSide(name) {
  for (const b of document.querySelectorAll(".stab")) b.classList.toggle("active", b.dataset.side === name);
  $("side-props").hidden = name !== "props";
  $("side-source").hidden = name !== "source";
  if (name === "source" && S.srcView) scrollToLine(S.srcView.line);
}

function wireSide() {
  for (const b of document.querySelectorAll(".stab")) b.addEventListener("click", () => showSide(b.dataset.side));
  $("src-back").addEventListener("click", () => historyStep(-1));
  $("src-fwd").addEventListener("click", () => historyStep(1));
  $("src-prev").addEventListener("click", () => markStep(-1));
  $("src-next").addEventListener("click", () => markStep(1));
  $("src-code").addEventListener("click", onCodeClick);
}

// ---------------------------------------------------------------- Verilog: what a double click opens
// The server generates the rig's top.sv as it is on the page and marks the
// lines that define the target; the modules it names come from rtl/ (and the
// chosen design). All from this checkout.

function verilogTarget(sel) {
  const ri = refIndex(), all = traceEdges();
  const pinRef = (key) => { const [cid, k] = String(key).split("."); const c = conn(cid), p = c && c.pins[k]; return p ? p.ref : null; };
  const usePins = (i) => {
    const u = S.setup.use[i] || {};
    if (u.module) return Object.values(wiresOf(u)).map(pinRef).filter(Boolean);
    const o = u.onboard && onboardDef(u.onboard);
    return o ? Object.values(onboardPins(o, u)).flatMap((ps) => ps.map((p) => p.ref)) : [];
  };
  const withDrop = (i) => Object.assign({use: i}, profileDrop(i) ? {refs: usePins(i)} : {});
  if (sel.kind === "vbit" || sel.kind === "vport") {
    const p = ports().find((q) => q.capability === sel.cap && q.signal === sel.signal);
    return p && {design_port: portName(p), parameter: p.width_parameter, module: "design_top", design: S.design};
  }
  if (sel.kind === "pin") return {refs: [pinRef(sel.conn + "." + sel.key)]};
  if (sel.kind === "ref") return {refs: [sel.ref]};
  if (sel.kind === "wire" || sel.kind === "mpin") {
    const w = wiresOf(S.setup.use[sel.use] || {})[sel.pin];
    return w ? {use: sel.use, refs: [pinRef(w)]} : withDrop(sel.use);
  }
  if (sel.kind === "edge") { const ed = all[sel.n]; return ed && {use: ed.use, refs: [ed.ref], design_port: ed.design_port, module: ed.via || undefined}; }
  if (sel.kind === "dseg") {
    const d = driverOf(sel), an = d && (sel.side === "pin" ? d.pin : d.design).get(sel.key);
    if (!an) return null;
    return sel.side === "pin" ? {use: sel.use, refs: [an.ref], module: sel.via} : {use: sel.use, design_port: an.ed.design_port, module: sel.via};
  }
  if (sel.kind === "driver") return {use: sel.use, module: sel.via};
  if (sel.kind === "use") return withDrop(sel.use);
  if (sel.kind === "onboard") { const i = S.setup.use.findIndex((u) => u.onboard === sel.id); return i >= 0 ? withDrop(i) : {refs: usePinsOfOnboard(sel.id)}; }
  if (sel.kind === "conn") return {refs: Object.values((conn(sel.id) || {pins: {}}).pins).map((p) => p.ref)};
  if (sel.kind === "board") return {board: true};
  return null;
}
function usePinsOfOnboard(id) {
  const o = onboardDef(id);
  if (!o) return [];
  const sets = o.device ? [o.pins || {}] : variantsOf(o).map((v) => v.pins);
  return [...new Set(sets.flatMap((pins) => Object.values(pins).flatMap((ps) => ps.map((p) => p.ref))))];
}

async function openVerilog(sel) {
  if (!sel) return;
  if (STATIC) { status("the Verilog view needs the editor server (./unifpga serve)", true); return; }
  const target = verilogTarget(sel);
  if (!target) return;
  showSide("source");
  $("src-title").textContent = "generating the Verilog for " + selLabel(sel) + "…";
  const r = await api("/api/verilog", {setup: S.setup, target});
  S.modules = new Set(r.modules.concat(["design_top"]));
  S.lastFiles = r.files;
  // the definition itself first: a driver's or design_top's own module, else the rig's top;
  // the other files are offered next to it
  const own = (sel.kind === "driver" || sel.kind === "vbit" || sel.kind === "vport") && r.files.length > 1 ? r.files[1] : r.files[0];
  S.related = r.files.filter((x) => x !== own);
  openView(own, own.highlight[0] || 1, own.highlight, selLabel(sel));
}

// ---- open files: one tab each, kept until closed. A file is known by its
// path (the generated top by its name); opening it again updates that tab.
function tabKey(file) { return file.path || "top.sv"; }
function adoptTab(file) {
  S.tabs = S.tabs || [];
  const tab = S.tabs.find((x) => tabKey(x) === tabKey(file));
  if (!tab) { file.rev = 1; S.tabs.push(file); return file; }
  if (tab !== file) {
    if (tab.text !== file.text && !dirty(tab)) {
      tab.text = file.text; tab.decl = null; tab.rev = (tab.rev || 1) + 1;
      if (tab.editable) tab.loaded = file.text;
    }
    if (!tab.pinned) Object.assign(tab, {title: file.title, highlight: file.highlight, note: file.note});
  }
  return tab;
}

// ---- the design chosen for the rig: a pinned tab, editable, not closable. It
// follows the choice: another design replaces it, another board or rig starts
// the Source panel afresh. A tab with unsaved edits is never dropped: it stays
// open (closable, still savable) next to the new one.
function dirty(tab) { return !!tab && tab.draft !== undefined && tab.draft !== tab.text; }
function tabText(tab) { return tab.draft !== undefined ? tab.draft : tab.text; }
async function pinDesign() {
  const rig = S.board && S.setup ? S.board.board + "/" + S.setup.id : null;
  const want = rig && S.design ? rig + "/" + S.design : null;
  if (STATIC || S.pinnedFor === want) return;
  const newRig = !S.pinnedFor || S.pinnedFor.split("/").slice(0, 2).join("/") !== rig;
  S.pinnedFor = want;
  S.tabs = (S.tabs || []).filter((x) => dirty(x) || !(x.pinned || newRig));
  for (const x of S.tabs) x.pinned = false;
  if (newRig) { S.srcHist = []; S.srcPos = 0; S.related = []; }
  S.srcHist = (S.srcHist || []).filter((v) => S.tabs.includes(v.file));
  S.srcPos = Math.max(0, Math.min(S.srcPos || 0, S.srcHist.length - 1));
  if (S.srcView && !S.tabs.includes(S.srcView.file)) S.srcView = null;
  if (!want) { renderSource(); return; }
  const design = S.design;
  const src = await api("/api/module", {name: "design_top", design});
  if (S.pinnedFor !== want) return;                      // the choice moved on meanwhile
  let tab = S.tabs.find((x) => x.path === src.path);     // its own earlier tab, still being edited
  if (!tab) {
    tab = {path: src.path, text: src.text, highlight: [], rev: 1};
    S.tabs.unshift(tab);
  } else {
    S.tabs.splice(S.tabs.indexOf(tab), 1); S.tabs.unshift(tab);
  }
  Object.assign(tab, {pinned: true, editable: true, design, loaded: tab.loaded || src.text,
                      title: "design " + design,
                      note: "The design chosen for this rig (" + S.setup.id + "): edit it here, then Save. Choosing another design or board replaces this tab."});
  openView(tab, src.line, [], "the design chosen for " + S.setup.id);
}
async function saveDesign(tab) {
  try {
    const r = await api("/api/design/save", {design: tab.design, text: tabText(tab), loaded: tab.loaded});
    Object.assign(tab, {text: r.text, loaded: r.text, draft: undefined, decl: null, rev: (tab.rev || 1) + 1});
    tab.editing = false;
    S.dt = null;                                          // the Designs page reads the requirements again
    renderSource();
    await changed("saved " + r.path);                     // which designs fit may have changed
  } catch (e) { status(e.message, true); }
}
function closeTab(tab) {
  const k = S.tabs.indexOf(tab);
  if (k < 0 || tab.pinned) return;
  if (dirty(tab) && !confirm("Close " + tab.path + " and lose its unsaved edits?")) return;
  S.tabs.splice(k, 1);
  S.srcHist = (S.srcHist || []).filter((v) => v.file !== tab);
  S.srcPos = Math.min(S.srcPos || 0, S.srcHist.length - 1);
  if (S.srcView && S.srcView.file === tab) {
    const next = S.tabs[Math.min(k, S.tabs.length - 1)];
    if (next) return openView(next, next.view.line, next.view.marks, next.view.why, true);
    S.srcView = null;
    $("src-code").replaceChildren(h("p", {class: "muted"}, "No file open. Double-click anything in the drawing to open the Verilog that defines it."));
    $("src-code").dataset.shown = "";
  }
  renderSource();
}

// ---- the viewer
function openView(file, line, marks, why, keepHistory) {
  file = adoptTab(file);
  const v = {file, line, marks: marks || [], why: why || ""};
  file.view = v;
  if (!keepHistory) {
    S.srcHist = (S.srcHist || []).slice(0, (S.srcPos || 0) + 1);
    S.srcHist.push(v); S.srcPos = S.srcHist.length - 1;
  }
  S.srcView = v;
  renderSource();
}

function historyStep(d) {
  const n = (S.srcPos || 0) + d;
  if (!S.srcHist || n < 0 || n >= S.srcHist.length) return;
  S.srcPos = n;
  openView(S.srcHist[n].file, S.srcHist[n].line, S.srcHist[n].marks, S.srcHist[n].why, true);
}

function markStep(d) {
  const v = S.srcView;
  if (!v || !v.marks.length) return;
  const k = v.marks.indexOf(v.line);
  v.line = v.marks[(k < 0 ? 0 : (k + d + v.marks.length) % v.marks.length)];
  v.file.view = v;
  renderMarks(); scrollToLine(v.line);
}

function renderMarksEmpty() { $("src-count").textContent = ""; for (const b of ["src-prev", "src-next"]) $(b).disabled = true; }

function renderMarks() {
  const v = S.srcView, k = v.marks.indexOf(v.line);
  $("src-count").textContent = v.marks.length ? (k >= 0 ? k + 1 : "–") + " of " + v.marks.length : "";
  for (const b of ["src-prev", "src-next"]) $(b).disabled = v.marks.length < 2;
  const box = $("src-code");
  for (const e of box.querySelectorAll(".ln.focus")) e.classList.remove("focus");
  const el = box.querySelector('[data-line="' + v.line + '"]');
  if (el) el.classList.add("focus");
}

function scrollToLine(n) {
  const box = $("src-code"), el = box.querySelector('[data-line="' + n + '"]');
  if (el && box.clientHeight) box.scrollTop = Math.max(0, el.offsetTop - box.clientHeight / 2 + el.offsetHeight / 2);
}

function renderSource() {
  const v = S.srcView, f = v && v.file;
  $("src-back").disabled = !(S.srcPos > 0);
  $("src-fwd").disabled = !(S.srcHist && S.srcPos < S.srcHist.length - 1);
  $("src-files").replaceChildren(...(S.tabs || []).map((x) => h("span", {class: "src-tab" + (x === f ? " cur" : "") + (x.pinned ? " pinned" : "")},
    h("button", {title: (x.pinned ? "the design chosen for this rig: " : "") + (x.path || "top.sv generated for the rig on the page (not a file)"),
                 onclick: () => openView(x, x.view.line, x.view.marks, x.view.why)},
      (x.pinned ? "📌 " : "") + (x.path ? x.path.split("/").pop() : "top.sv") + (x.pinned ? " (" + x.design + ")" : "") + (dirty(x) ? " ●" : "")),
    x.pinned ? null : h("button", {class: "x", title: "Close", onclick: () => closeTab(x)}, "×"))));
  renderEditBar(f);
  const rel = (S.related || []).filter((x) => !f || tabKey(x) !== tabKey(f));
  $("src-related").replaceChildren(...(rel.length && f ? ["also: "].concat(rel.map((x) => h("button", {title: x.path || "generated",
    onclick: () => openView(x, x.highlight[0] || 1, x.highlight, v.why)}, x.title))) : []));
  if (!f) { $("src-title").textContent = ""; $("src-note").textContent = ""; renderMarksEmpty(); return; }
  $("src-title").textContent = (f.path || f.title) + (v.why ? "  —  " + v.why : "");
  $("src-note").textContent = f.note || "";
  const box = $("src-code");
  if (f.editing) {
    if (box.dataset.shown !== tabKey(f) + "#edit") {
      box.replaceChildren(codeEditor(f, v.line));
      box.dataset.shown = tabKey(f) + "#edit";
    }
    renderMarksEmpty();
    return;
  }
  if (box.dataset.shown !== tabKey(f) + "#" + (f.rev || 1)) {
    renderCode(box, tabText(f));
    box.dataset.shown = tabKey(f) + "#" + (f.rev || 1);
    f.decl = f.decl || declarations(tabText(f));
    for (const e of box.querySelectorAll(".id")) if (f.decl.has(e.textContent)) e.classList.add("nav");
  }
  const marks = new Set(v.marks);
  for (const e of box.querySelectorAll(".ln")) e.classList.toggle("hl", marks.has(Number(e.dataset.line)));
  for (const e of box.querySelectorAll(".occ")) e.classList.remove("occ");
  renderMarks();
  scrollToLine(v.line);
}

// Edit / View, Save, Revert for an editable tab (the pinned design)
function renderEditBar(f) {
  const bar = $("src-edit");
  if (!f || !f.editable || STATIC) { bar.replaceChildren(); return; }
  const leave = () => { f.editing = false; f.rev = (f.rev || 1) + 1; f.decl = null; renderSource(); };
  bar.replaceChildren(
    f.editing ? h("button", {onclick: leave, title: "Back to the coloured, navigable view (edits are kept)"}, "View")
              : h("button", {onclick: () => { f.editing = true; renderSource(); }}, "Edit"),
    h("button", {onclick: () => saveDesign(f), disabled: !dirty(f)}, "Save"),
    h("button", {disabled: !dirty(f), onclick: () => { if (confirm("Drop the unsaved edits to " + f.path + "?")) { f.draft = undefined; leave(); } }}, "Revert"),
    h("span", {class: "muted"}, (dirty(f) ? " unsaved edits" : " saved") +
      (f.editing ? " · " + MODS.points + "-click or F12 on a name: its definition" : "")));
}
// an editor that stays coloured: the text is typed into a transparent
// textarea over the same text coloured by highlightVerilog, with line
// numbers beside it; the three scroll together and recolour as you type
function codeEditor(f, line) {
  const gutter = h("div", {class: "ed-gutter", "aria-hidden": "true"});
  const layer = h("pre", {class: "ed-layer code", "aria-hidden": "true"});
  const ta = h("textarea", {class: "ed-input", spellcheck: "false", autocapitalize: "off", autocomplete: "off", wrap: "off"});
  ta.value = tabText(f);
  const paint = () => {
    const lines = highlightVerilog(ta.value);
    layer.replaceChildren(...lines.flatMap((toks, k) => {
      const out = toks.map(([cls, s]) => cls ? h("span", {class: cls}, s) : s);
      return k < lines.length - 1 ? out.concat("\n") : out.concat("\n ");   // a last newline keeps the heights equal
    }));
    gutter.textContent = lines.map((_, k) => k + 1).join("\n") + "\n";
    sync();
  };
  const sync = () => {
    // plain offsets, not transforms: a transformed layer is composited on its
    // own and was left partly unpainted
    layer.style.top = -ta.scrollTop + "px";
    layer.style.left = -ta.scrollLeft + "px";
    gutter.style.marginTop = -ta.scrollTop + "px";
  };
  let pending = 0;
  ta.addEventListener("input", () => {
    f.draft = ta.value; renderTabsOnly();
    cancelAnimationFrame(pending); pending = requestAnimationFrame(paint);
  });
  ta.addEventListener("scroll", sync);
  // ⌘-click (Ctrl-click) or F12 on a name: its definition, as a click does in the coloured view
  ta.addEventListener("click", (e) => { if (e[MODS.pointsKey]) { e.preventDefault(); goToDefinition(f, ta); } });
  ta.addEventListener("keydown", (e) => {          // Tab indents instead of leaving the editor
    if (e.key === "F12") { e.preventDefault(); goToDefinition(f, ta); return; }
    if (e.key !== "Tab" || e.metaKey || e.ctrlKey || e.altKey) return;
    e.preventDefault();
    const s = ta.selectionStart;
    ta.setRangeText("    ", s, ta.selectionEnd, "end");
    ta.dispatchEvent(new Event("input"));
  });
  const wrap = h("div", {class: "ed-wrap"}, gutter, h("div", {class: "ed-body"}, layer, ta));
  paint();
  requestAnimationFrame(() => {
    const lines = ta.value.split("\n"), n = Math.max(0, Math.min(line || 1, lines.length) - 1);
    ta.focus();
    ta.selectionStart = ta.selectionEnd = lines.slice(0, n).join("\n").length + (n ? 1 : 0);
    ta.scrollTop = Math.max(0, n * parseFloat(getComputedStyle(ta).lineHeight) - ta.clientHeight / 2);
    sync();
  });
  return wrap;
}

// the token under the editor's caret: {cls, text, line, before} (before: the text in front of it on its line)
function tokenAtCaret(ta) {
  const text = ta.value, at = ta.selectionStart;
  const line = text.slice(0, at).split("\n").length, col = at - (text.lastIndexOf("\n", at - 1) + 1);
  let x = 0;
  for (const [cls, s] of highlightVerilog(text)[line - 1] || []) {
    if (col >= x && col <= x + s.length && /^[A-Za-z_]/.test(s)) return {cls, text: s, line, before: text.split("\n")[line - 1].slice(0, x)};
    x += s.length;
  }
  return null;
}
function goToDefinition(f, ta) {
  const tok = tokenAtCaret(ta);
  if (!tok) return;
  const go = (p) => p.catch((x) => status(x.message, true));
  if (tok.cls === "m") return go(openModule(tok.text, null, "module " + tok.text));
  if (/\.\s*$/.test(tok.before)) {                          // .port(...) of an instance
    const mod = instanceModuleAt(ta.value, tok.line);
    if (mod) return go(openModule(mod, tok.text, mod + "." + tok.text));
  }
  const decl = declarations(ta.value).get(tok.text);
  if (!decl) { status("no definition of " + tok.text + " in this file"); return; }
  const lines = ta.value.split("\n");                        // in this file: the caret goes there
  ta.selectionStart = ta.selectionEnd = lines.slice(0, decl - 1).join("\n").length + (decl > 1 ? 1 : 0);
  ta.scrollTop = Math.max(0, (decl - 1) * parseFloat(getComputedStyle(ta).lineHeight) - ta.clientHeight / 2);
  ta.focus();
  status(tok.text + ": line " + decl);
}

// the tab strip and edit bar only (typing must not rebuild the editor)
function renderTabsOnly() {
  const f = S.srcView && S.srcView.file;
  for (const b of $("src-files").querySelectorAll(".src-tab.cur button:first-child"))
    b.textContent = (f.pinned ? "📌 " : "") + (f.path ? f.path.split("/").pop() : "top.sv") + (f.pinned ? " (" + f.design + ")" : "") + (dirty(f) ? " ●" : "");
  renderEditBar(f);
}

function renderCode(box, text) {
  box.replaceChildren(...highlightVerilog(text).map((toks, k) => {
    const ln = h("div", {class: "ln", "data-line": k + 1}, h("span", {class: "no"}, String(k + 1)));
    for (const [cls, s] of toks) ln.append(cls ? h("span", {class: cls}, s) : s);
    return ln;
  }));
}

// ---- SystemVerilog tokens: [class, text] per line; classes k keyword, t type,
// c comment, s string, n number, d directive, f system task, m module name, id identifier
const SV_KEYWORDS = new Set(("module endmodule macromodule input output inout parameter localparam assign always always_ff always_comb " +
  "always_latch initial final begin end if else case casez casex endcase default for foreach while do repeat forever generate endgenerate " +
  "genvar function endfunction task endtask return posedge negedge or and not xor typedef enum struct packed union interface endinterface " +
  "modport import export package endpackage automatic static unique unique0 priority wait disable fork join join_any join_none signed unsigned " +
  "const break continue inside assert assume cover property endproperty sequence endsequence").split(" "));
const SV_TYPES = new Set("wire logic reg bit byte int integer shortint longint real realtime time tri tri0 tri1 wand wor supply0 supply1 var string void".split(" "));
const SV_TOKEN = /(\/\/.*$)|(\/\*)|("(?:[^"\\]|\\.)*")|(`[A-Za-z_]\w*)|(\$[A-Za-z_]\w*)|(\d*'[sS]?[bBoOdDhH]\s*[0-9a-fA-FxXzZ_?]+|\d[\d_]*(?:\.\d+)?|'[01xXzZ])|([A-Za-z_]\w*)|(\s+|.)/g;

function highlightVerilog(text) {
  let inBlock = false;
  return text.split("\n").map((line) => {
    const out = [];
    let i = 0;
    if (inBlock) {
      const e = line.indexOf("*/");
      if (e < 0) return [["c", line]];
      out.push(["c", line.slice(0, e + 2)]); i = e + 2; inBlock = false;
    }
    SV_TOKEN.lastIndex = i;
    let m;
    while (i < line.length && (m = SV_TOKEN.exec(line))) {
      i = SV_TOKEN.lastIndex;
      const [tok, lc, bc, str, dir, sys, num, id] = m;
      if (lc) out.push(["c", tok]);
      else if (bc) {
        const e = line.indexOf("*/", m.index + 2);
        if (e < 0) { out.push(["c", line.slice(m.index)]); inBlock = true; break; }
        out.push(["c", line.slice(m.index, e + 2)]); i = e + 2; SV_TOKEN.lastIndex = i;
      } else if (str) out.push(["s", tok]);
      else if (dir) out.push(["d", tok]);
      else if (sys) out.push(["f", tok]);
      else if (num) out.push(["n", tok]);
      else if (id) out.push([SV_KEYWORDS.has(id) ? "k" : SV_TYPES.has(id) ? "t" : S.modules && S.modules.has(id) ? "m" : "id", tok]);
      else out.push(["", tok]);
    }
    return out;
  });
}

// where each name is declared in a file (1-based line): modules, ports,
// nets, parameters (also continued parameter lists), instances, functions,
// tasks, typedefs and named blocks. Linear in the file.
function declarations(text) {
  const decl = new Map();
  const add = (name, k) => { if (name && !decl.has(name) && !SV_KEYWORDS.has(name) && !SV_TYPES.has(name)) decl.set(name, k + 1); };
  let inParams = false;
  text.split("\n").forEach((raw, k) => {
    const l = raw.replace(/\/\/.*$/, "");
    let m;
    if ((m = l.match(/^\s*module\s+([A-Za-z_]\w*)/))) add(m[1], k);
    if ((m = l.match(/^\s*(?:input|output|inout|wire|logic|reg|tri|integer|genvar|int|bit|byte|parameter|localparam)\b(.*)$/))) {
      const rest = m[1].replace(/\b(?:wire|logic|reg|signed|unsigned|int|integer|bit|byte|type|var)\b/g, " ").replace(/\[[^\]]*\]/g, " ");
      for (const part of rest.split(",")) { const id = part.trim().match(/^([A-Za-z_]\w*)/); if (id) add(id[1], k); }
      inParams = /^\s*(?:parameter|localparam)\b/.test(l) && /,\s*$/.test(l);
    } else if (inParams && (m = l.match(/^\s*([A-Za-z_]\w*)\s*=/))) { add(m[1], k); inParams = /,\s*$/.test(l); }
    else inParams = false;
    if ((m = l.match(/^\s*([A-Za-z_]\w*)\s*(?:#\s*\(.*\)\s*)?([A-Za-z_]\w*)\s*\(/)) && !SV_KEYWORDS.has(m[1]) && !SV_TYPES.has(m[1])) add(m[2], k);
    if ((m = l.match(/^\s*\)\s*([A-Za-z_]\w*)\s*\(/))) add(m[1], k);
    if ((m = l.match(/\b(?:function|task)\b.*?\b([A-Za-z_]\w*)\s*[(;]/))) add(m[1], k);
    if ((m = l.match(/\bbegin\s*:\s*([A-Za-z_]\w*)/))) add(m[1], k);
    if ((m = l.match(/^\s*typedef\b.*\b([A-Za-z_]\w*)\s*;/))) add(m[1], k);
  });
  return decl;
}

// the module a `.port (...)` connection on line n belongs to: the nearest
// instance head above it
function instanceModuleAt(text, n) {
  const lines = text.split("\n");
  for (let k = n - 1; k >= 0; k--) {
    const m = lines[k].match(/^\s*([A-Za-z_]\w*)\s*(#|[A-Za-z_]\w*\s*\()/);
    if (m && S.modules && S.modules.has(m[1])) return m[1];
    if (/;\s*$/.test(lines[k]) && k < n - 1) return null;
  }
  return null;
}

async function openModule(name, focusName, why) {
  const src = await api("/api/module", {name, design: name === "design_top" ? S.design : undefined});
  const file = adoptTab((S.tabs || []).find((x) => x.path === src.path) ||
                        {path: src.path, title: "module " + name, text: src.text, highlight: [src.line], note: ""});
  file.decl = file.decl || declarations(tabText(file));
  const line = focusName && file.decl.has(focusName) ? file.decl.get(focusName) : src.line;
  openView(file, line, [line], why);
}

function onCodeClick(e) {
  const tok = e.target.closest(".id, .m");
  if (!tok || !S.srcView) return;
  const f = S.srcView.file, name = tok.textContent, box = $("src-code");
  for (const x of box.querySelectorAll(".occ")) x.classList.remove("occ");
  for (const x of box.querySelectorAll(".id, .m")) if (x.textContent === name) x.classList.add("occ");
  const line = Number(tok.closest(".ln").dataset.line);
  const prev = tok.previousSibling && tok.previousSibling.textContent;
  const go = (p) => p.catch((x) => status(x.message, true));
  if (tok.classList.contains("m")) return go(openModule(name, null, "module " + name));
  if (prev && /\.\s*$/.test(prev)) {               // .port(...) of an instance: the port in that module
    const mod = instanceModuleAt(tabText(f), line);
    if (mod) return go(openModule(mod, name, mod + "." + name));
  }
  f.decl = f.decl || declarations(tabText(f));
  if (f.decl.has(name) && f.decl.get(name) !== line) openView(f, f.decl.get(name), [f.decl.get(name)], name);
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
    const sharedHere = sel.kind === "vbit" ? [sel.bit] : [...sharedBits(p).keys()];
    const pinsHere = new Set(traceEdges().filter((e) => e.design_port === portName(p) && (sel.kind !== "vbit" || (e.bits || []).includes(sel.bit)) &&
                                                        sharedRefs().has(e.ref)).map((e) => e.ref));
    for (const ref of pinsHere) d.append(h("p", {class: "note"}, "Conflicted: pin " + ref + " is " + sharedRefText(ref) + "."));
    for (const b of sharedHere) if (sharedBitText(p, b)) d.append(h("p", {class: "note"}, sharedBitText(p, b) +
      (p.direction === "hw_to_user" ? " — the build ORs the parts' bits (the rig's design-wiring profile or lab_bits give them the same design bit)" : "")));
    const facts = [["Width", p.width_parameter
      ? widthText(p) + " — a design_top parameter (" + portName(p) + "[" + p.width_parameter + " - 1 : 0]): this rig sets it, other rigs give other widths, and a design reads " + p.width_parameter
      : (p.width || 0) + " bit" + (p.width === 1 ? "" : "s") + ", fixed by the design_top interface"]];
    if (capDef(p.capability).summary && p.providers.length) {
      const req = sizeRequirement(p.capability);
      facts.push([p.capability.replace(/_/g, " ").replace(/^./, (ch) => ch.toUpperCase()), capSummary(p.capability) +
                  " on this rig" + (req ? "; a design states its minimum with a line like `" + req + "`" : "")]);
    }
    if (p.providers.length) facts.push(["Set by", providersText(p)]);
    d.append(h("table", {class: "facts"}, ...facts.map(([k, v]) => h("tr", {}, h("th", {}, k), h("td", {}, v)))));
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
    if (sharedRefText(pin.ref)) d.append(h("p", {class: "note"}, "This pin is " + sharedRefText(pin.ref) + "."));
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
  } else if (sel.kind === "board") {
    d.append(h("h4", {}, S.board.board + (S.board.verified ? "" : " (layout not verified)")));
    d.append(h("div", {}, S.board.connectors.length + " connectors, " + S.board.onboard.length + " on-board devices"));
    d.append(h("p", {class: "muted"}, "Double-click the board for the generated top module's ports: every FPGA pin this rig uses."));
  } else if (sel.kind === "ref") {
    const has = (x) => variantsOf(x).some((v) => Object.values(v.pins).some((ps) => ps.some((pp) => pp.ref === sel.ref)));
    const o = S.board.onboard.find(has);
    const ou = o && uses.find((u) => u.onboard === o.id);
    const pp = o && Object.entries(onboardPins(o, ou)).flatMap(([s, ps]) => ps.map((x) => Object.assign({s}, x))).find((x) => x.ref === sel.ref);
    d.append(h("h4", {}, (o ? o.label + " " : "") + sel.ref));
    if (pp) d.append(h("div", {}, "signal " + pp.s + ", FPGA pin " + (pp.pin || "?")));
    const eds = ((S.ev && S.ev.trace && S.ev.trace.edges) || []).filter((ed) => ed.ref === sel.ref);
    edgeList(d, sel.ref);
    if (!eds.length) d.append(h("p", {}, "No design port reaches this pin."));
  } else if (sel.kind === "use") {
    useDetails(d, sel.use);
  } else if (sel.kind === "onboard" && onboardDef(sel.id) && onboardDef(sel.id).device) {
    // a device the board has but no peripheral models yet: its pins, not usable
    const o = onboardDef(sel.id);
    d.append(h("h4", {}, o.label));
    d.append(h("p", {class: "note"}, "No peripheral model yet for this " + (o.device.kind || "device") +
      ": the rig shows it so the board is complete, but a design cannot use it until one exists. Its pins are in the pinmap bank " + o.device.bank + "."));
    for (const [s, ps] of Object.entries(o.pins || {})) d.append(h("div", {}, s + ": " + ps.map((x) => x.ref + " = " + (x.pin || "?")).join(", ")));
  } else if (sel.kind === "onboard") {
    const o = onboardDef(sel.id), i = uses.findIndex((u) => u.onboard === sel.id), vs = variantsOf(o);
    const cur = variantOf(o, uses[i]);
    d.append(h("h4", {}, o.label + " (" + cur.attach.peripheral + ")"));
    if (vs.length > 1) {
      // the ways this part can be used, one at a time
      const pick = h("select", {disabled: STATIC ? "" : null, onchange: (e) => {
        if (i >= 0) { const u = S.setup.use[i]; u.variant = e.target.value; delete u.params; changed(o.label + " used as " + e.target.value); }
      }}, ...vs.map((v) => h("option", {value: v.id, selected: v === cur ? "" : null}, v.label)));
      d.append(h("div", {}, "Used as: ", pick, h("span", {class: "muted"}, "  (" + vs.length + " ways this part can be used)")));
      S.onboardPick = pick;
    }
    if (i >= 0 && profileDrop(i)) d.append(h("p", {class: "note"}, "Not connected to the design: " + dropText(i) + "."));
    if (i >= 0 && partStatus(i)) d.append(h("p", {class: "note"}, "Not connected to the design: " + partText(i)));
    for (const [s, ps] of Object.entries(cur.pins)) d.append(h("div", {}, s + ": " + ps.map((x) => x.ref + " = " + (x.pin || "?")).join(", ")));
    if (i >= 0) { for (const x of portsOfUse(i)) d.append(chain(["design " + x.port.signal + (x.bits.length ? "[" + x.bits.join(",") + "]" : ""), o.label + (x.via ? " via " + x.via : "")])); }
    if (!STATIC) {
      if (i >= 0) { paramForm(d, i); d.append(h("button", {onclick: () => { S.setup.use.splice(i, 1); changed("stopped using " + o.label); }}, "Do not use")); }
      else d.append(h("button", {onclick: () => {
        const use = {onboard: o.id};
        if (vs.length > 1) use.variant = S.onboardPick ? S.onboardPick.value : vs[0].id;
        S.setup.use.push(use); changed("using " + o.label + (use.variant ? " as " + use.variant : ""));
      }}, "Use"));
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
    rows.push(["Bit relation", relationText(ed, p)]);
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
    const rel = all[an.edges[0]].relation;
    d.append(h("p", {}, rel === "or" ? "The driver ORs these design bits onto one pin:"
      : an.edges.length > 1
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
  if (partStatus(i)) d.append(h("p", {class: "note"}, (partStatus(i).connected ? "Only partly connected to the design: " : "Not connected to the design: ") + partText(i)));
  for (const p of problemsOfUse(i)) d.append(h("div", {class: "conflict"}, h("p", {class: "note"}, "Conflict: " + p.message), fixButtons(p)));
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
    for (const p of Object.keys(m.unwired || {}))
      if (!w[p]) d.append(h("p", {class: "note"}, p + " is not wired: " + unwiredTie(m, p) + " on the module — " + m.unwired[p].why + "."));
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

// the buttons that resolve a problem between parts (the server's `resolve`:
// re-wire a module to free pins, or remove a part, the parts named)
function fixButtons(p) {
  if (STATIC || !(p.resolve || []).length) return null;
  return h("div", {class: "fixes"}, ...p.resolve.map((f) => h("button", {onclick: () => applyFix(f)}, f.label)));
}
async function applyFix(f) {
  if (f.op === "autowire") return autoWire(f.use);
  if (f.op === "remove") {
    const label = useLabel(S.setup.use[f.use]);
    S.setup.use.splice(f.use, 1);
    S.sel = null;
    return changed("removed " + label + " (resolving a conflict)");
  }
}
// the problems a part is in, with their buttons
function problemsOfUse(i) { return ((S.ev && S.ev.problems) || []).filter((p) => (p.uses || []).includes(i)); }

async function autoWire(i) {
  try {
    const got = await api("/api/autowire", {setup: S.setup, use: i});
    const use = S.setup.use[i];
    delete use.plug; delete use.wires;
    Object.assign(use, got);
    S.pending = null;
    S.sel = {kind: "use", use: i};
    const what = "auto-wired " + useLabel(use) + (got.plug ? " (plugged into " + got.plug.connector + (got.plug.row === "all" ? "" : " row " + got.plug.row) + (got.plug.reversed ? ", turned round" : "") + ")" : "");
    await changed(what);
    const st = partStatus(i);
    if (st) status(what + " — the pins are free, but it " + (st.connected ? "reaches the design only in part: " : "does not reach the design: ") + partText(i), true);
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
  const perId = use.module ? moduleDef(use.module).peripheral : use.onboard ? variantOf(onboardDef(use.onboard), use).attach.peripheral : null;
  const per = perId && S.board.peripherals[perId];
  if (!per || !Object.keys(per.parameters).length) return;
  const base = use.onboard ? (variantOf(onboardDef(use.onboard), use).attach.params || {}) : {};
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
  for (const p of probs) pl.append(h("li", {class: p.level}, p.level + ": " + p.message, fixButtons(p)));
  $("config-text").textContent = (S.ev && S.ev.configuration_text) || "";
}

// a rig's default toolchain / chip (`toolchain:` / `part:`) and every one it is
// checked with (`toolchains:` / `parts:`, the default first; left out when it is the only one)
function setDefault(key, listKey, value) {
  S.setup[key] = value;
  if (S.setup[listKey]) S.setup[listKey] = [value, ...S.setup[listKey].filter((v) => v !== value)];
}
function renderTargets() {
  const box = $("targets");
  if (!box || !S.setup || !S.board) return;
  const parts = S.board.parts || [];
  $("part-label").hidden = parts.length < 2;
  if (parts.length > 1) fillSelect($("part"), [...new Set([S.setup.part, ...parts].filter(Boolean))], S.setup.part);
  const row = (label, key, listKey, all) => {
    if (all.length < 2) return null;
    const have = S.setup[listKey] || [S.setup[key]];
    return h("span", {class: "checked-with"}, label + " ", ...all.map((v) => {
      const cb = h("input", {type: "checkbox", "data-target": listKey + ":" + v});
      cb.checked = have.includes(v);
      cb.disabled = v === S.setup[key];
      cb.addEventListener("change", () => {
        const next = (S.setup[listKey] || [S.setup[key]]).filter((x) => x !== v);
        if (cb.checked) next.push(v);
        if (next.length > 1) S.setup[listKey] = next; else delete S.setup[listKey];
        changed((cb.checked ? "also checked with " : "no longer checked with ") + v);
      });
      return h("label", {class: "target"}, cb, v);
    }));
  };
  const aliases = Object.keys(S.setup.aliases || {});
  box.replaceChildren(...[row("checked with", "toolchain", "toolchains", S.board.toolchains),
                          row("chips", "part", "parts", parts),
                          aliases.length ? h("span", {class: "aliases", title: "ids of the per-toolchain / per-chip copies this rig replaced; they still build it"},
                                             "also known as " + aliases.join(", ")) : null].filter(Boolean));
}

function render() {
  // a selection whose part has been removed or moved away is dropped
  const n = (S.setup && S.setup.use || []).length;
  if (S.sel && ["use", "wire", "dseg", "driver"].includes(S.sel.kind) && !(S.sel.use < n)) S.sel = null;
  if (S.pending && !(S.pending.use < n)) S.pending = null;
  draw(); details(); tables(); designChoices(); renderTitle(); headerActions(); renderTargets();
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
  const add = $("add-module"), uses = S.setup.use || [];
  // exclusive capabilities already provided, and by which part
  const taken = new Map();
  uses.forEach((u, i) => { for (const c of providedCaps(usePeripheral(u))) if (capAggregation(c) === "exclusive" && !taken.has(c) && !profileDrop(i)) taken.set(c, useLabel(u)); });
  const text = (m) => {
    const n = uses.filter((u) => u.module === m.id).length;
    const clash = providedCaps(m.peripheral).filter((c) => taken.has(c)).map((c) => c + " already provided by " + taken.get(c));
    return m.name + " (" + m.peripheral + ")" + (n ? " — in the rig" + (n > 1 ? " ×" + n : "") : "") +
           (clash.length ? " — a new one would not reach the design: " + clash.join(", ") : "");
  };
  const want = ["Add module…"].concat(S.board.modules.map(text));
  if ([...add.options].map((o) => o.textContent).join("\n") !== want.join("\n"))
    add.replaceChildren(h("option", {value: ""}, want[0]), ...S.board.modules.map((m, k) => h("option", {value: m.id}, want[k + 1])));
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
    S.design = good.length ? good[0][0] : null;
  const items = [h("div", {class: "designs-head"}, fit ? "Fit this rig (" + good.length + ")" : "Designs (fix the rig's problems to check them)")];
  for (const [d] of good)
    items.push(h("div", {class: "design fit" + (d === S.design ? " chosen" : ""), "data-design": d,
                         onclick: () => { S.design = d; designChoices(); }}, d));
  if (bad.length) items.push(h("div", {class: "designs-head"}, "Do not fit (" + bad.length + ")"));
  for (const [d, unmet] of bad)
    items.push(h("div", {class: "design unfit", "data-design": d, title: unmet.join("\n")}, d, h("span", {class: "why"}, unmet.join("; "))));
  box.replaceChildren(...items);
  pinDesign().catch((e) => status(e.message, true));
}

// ---- the New setup form
function openNewSetupForm() {
  const f = $("new-setup-form");
  let n = 1, id = S.board.board + "_rig";
  while (setupIdTaken(id)) id = S.board.board + "_rig_" + (++n);
  $("ns-id").value = id;
  fillSelect($("ns-toolchain"), S.board.toolchains);
  $("ns-toolchain").value = (S.setup && S.setup.toolchain) || S.board.toolchains[0];
  const cur = S.setup && S.setup.id;
  $("ns-current").textContent = cur ? cur : "the rig on the page";
  f.querySelector('input[value="copy"]').disabled = !S.setup;
  f.querySelector('input[value="empty"]').checked = true;
  newSetupProblem(false);
  f.hidden = false;
  $("ns-id").focus(); $("ns-id").select();
}
function setupIdTaken(id) { return (S.board.setups || []).includes(id) || [...$("setup").options].some((o) => o.value === id); }
// what is wrong with the name typed (shown under the form), or ""
function newSetupProblem(show) {
  const id = $("ns-id").value.trim();
  const why = !/^[a-z0-9_]{1,80}$/.test(id) ? "a name is 1-80 characters of a-z, 0-9 and _"
            : setupIdTaken(id) ? "a setup called " + id + " exists already" : "";
  $("ns-msg").textContent = why || "It is saved only when you press Save.";
  $("ns-msg").className = why && show ? "err" : "muted";
  $("ns-create").disabled = !!why;
  return why;
}
function createNewSetup() {
  if (newSetupProblem(true)) return;
  const id = $("ns-id").value.trim(), toolchain = $("ns-toolchain").value;
  const copy = $("new-setup-form").querySelector('input[name="ns-from"]:checked').value === "copy" && S.setup;
  // an empty rig starts with an on-board part for every capability a rig needs (the clock)
  const needed = (S.board.capabilities || []).filter((c) => c.required).map((c) => c.id);
  const starts = needed.map((cid) => S.board.onboard.find((o) => variantsOf(o).some((v) => v.attach && providedCaps(v.attach.peripheral).includes(cid)))).filter(Boolean);
  const base = copy ? clone(S.setup) : {board: S.board.board, use: [...new Set(starts)].map((o) => ({onboard: o.id}))};
  base.id = id; delete base.notes;
  delete base.aliases;                  // the ids the copied rig replaced stay its own
  S.setup = base; S.sel = null;
  setDefault("toolchain", "toolchains", toolchain);
  $("setup").append(h("option", {value: id}, id)); $("setup").value = id;
  $("toolchain").value = toolchain;
  $("new-setup-form").hidden = true;
  changed("new setup " + id + (copy ? " (a copy)" : "") + " — press Save to keep it");
}

function staleWarning() {
  if (S.ev && S.ev.server_stale)
    status("unifpga's code changed since this server started: restart ./unifpga serve and reload the page", true);
}

function showTab(name) {
  for (const b of document.querySelectorAll(".tab")) b.classList.toggle("active", b.dataset.tab === name);
  for (const p of ["designs", "rig", "config"]) { const e = $("tab-" + p); if (e) e.hidden = name !== p; }
  // the rig's own controls only where a rig is shown
  for (const id of ["title", "add-module", "remove-sel", "save"]) $(id).style.visibility = name === "designs" ? "hidden" : "";
  if (name === "rig") applyView();
  if (name === "designs") loadDesigns();
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
  let r;
  try { r = await fetch(path, opt); }
  catch (e) { throw new Error("the editor server does not answer (" + e.message + "): is ./unifpga serve running? Restart it and reload"); }
  const data = r.headers.get("Content-Type").startsWith("application/json") ? await r.json() : null;
  if (r.status === 404 && path.startsWith("/api/") && data && data.error === "not found")
    throw new Error("the editor server does not know " + path + ": it is older than this page — restart ./unifpga serve");
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
  $("toolchain").addEventListener("change", (e) => { setDefault("toolchain", "toolchains", e.target.value); changed("toolchain " + e.target.value); });
  $("part").addEventListener("change", (e) => { setDefault("part", "parts", e.target.value); changed("chip " + e.target.value); });
  for (const b of document.querySelectorAll(".tab")) b.addEventListener("click", () => showTab(b.dataset.tab));
  $("add-module").addEventListener("change", (e) => { const id = e.target.value; e.target.value = ""; if (id) addModule(id); });
  wireSide();
  $("remove-sel").addEventListener("click", removeSelected);
  // New setup: an inline form (name, toolchain, empty rig or a copy of this one)
  $("new-setup").addEventListener("click", () => openNewSetupForm());
  $("ns-cancel").addEventListener("click", () => { $("new-setup-form").hidden = true; });
  $("new-setup-form").addEventListener("keydown", (e) => { if (e.key === "Escape") $("new-setup-form").hidden = true; });
  $("ns-id").addEventListener("input", () => newSetupProblem(true));
  $("new-setup-form").addEventListener("submit", (e) => { e.preventDefault(); createNewSetup(); });
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
  wireSplitter();
  if (STATIC) {
    document.body.classList.add("readonly");
    $("tab-designs").remove(); document.querySelector('.tab[data-tab="designs"]').remove();   // needs the server
    showTab("rig");
    S.board = STATIC.board; S.setup = STATIC.setup; S.ev = STATIC.evaluation;
    fillSelect($("board"), [S.board.board]); fillSelect($("setup"), [S.setup.id]);
    for (const b of document.querySelectorAll(".tab")) b.addEventListener("click", () => showTab(b.dataset.tab));
    renderModules();
    status("read-only view (./unifpga serve to edit)");
    render();
    return;
  }
  wire();
  $("d-search").addEventListener("input", renderDesigns);
  $("d-board").addEventListener("change", renderDesigns);
  $("d-fitonly").addEventListener("change", renderDesigns);
  const boards = await api("/api/boards");
  fillSelect($("board"), boards);
  const q = new URLSearchParams(location.search);
  let b = q.get("board");
  if (!b && q.get("setup")) b = (await api("/api/setup/" + encodeURIComponent(q.get("setup")))).board;
  if (!boards.includes(b)) b = boards[0];
  $("board").value = b;
  await loadBoard(b, q.get("setup"));
  // the Designs page unless the link is about a rig
  showTab(q.get("tab") || (q.get("selftest") || q.get("sel") || q.get("setup") ? "rig" : "designs"));
  if (q.get("selftest")) return selftest();
  if (q.get("sel")) { S.sel = parseSel(q.get("sel")); render(); }
  if (q.get("side") === "source" || q.get("side") === "props") showSide(q.get("side"));
  if (q.get("edit")) {                  // ?edit=1: the pinned design, open for editing
    for (let k = 0; k < 100 && !(S.tabs || []).some((x) => x.pinned); k++) await new Promise((r) => setTimeout(r, 50));
    const pin = (S.tabs || []).find((x) => x.pinned);
    if (pin) { showSide("source"); pin.editing = true; openView(pin, pin.view.line, [], pin.view.why, true); }
  }
}

// ?side=source | props (the right panel's tab), ?edit=1 (the pinned design in the editor)
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
    // a module's pin through a driver, bit for bit: its wire, its design port and its panel
    const ri = refIndex(), edgesNow = S.ev.trace.edges;
    const byWire = (ed) => { const u = S.setup.use[ed.use] || {}; if (!u.module) return null;
      const mp = Object.entries(wiresOf(u)).find(([, w]) => w === ri[ed.ref]); return mp ? mp[0] : null; };
    const n1 = edgesNow.findIndex((ed) => ed.via && ed.bit !== null && ed.relation === "bit" && byWire(ed));
    if (n1 >= 0) {
      const ed = edgesNow[n1], u = S.setup.use[ed.use], m = moduleDef(u.module), mp = byWire(ed);
      const p = ports().find((q) => q.design_port === ed.design_port);
      select({kind: "wire", use: ed.use, pin: mp});
      const hiW = highlight();
      ok("a module pin lights the design bit it serves", hiW.vbits.has(p.capability + "." + p.signal + "." + ed.bit));
      select({kind: "vport", cap: p.capability, signal: p.signal});
      const hiP = highlight();
      const mine = new Set(edgesNow.filter((x) => x.use === ed.use && x.design_port === ed.design_port).map((x) => byWire(x)).filter(Boolean));
      ok("a design port lights exactly the module pins serving it", [...mine].every((w) => hiP.wires.has(ed.use + "." + w)) &&
         [...hiP.wires].filter((w) => w.startsWith(ed.use + ".")).length === mine.size);
      ok("the trace names the driver", $("details").textContent.includes(ed.via + " ("));
      select({kind: "edge", n: n1});
      const txt = $("details").textContent, [cid, key] = ri[ed.ref].split(".");
      ok("a connection's panel gives its driver, pinmap, FPGA, header and module pin",
         txt.includes("through the " + ed.via + " driver") && txt.includes(ed.ref) && txt.includes(ed.pin) &&
         txt.includes(conn(cid).label + " pin " + key) && txt.includes("Module pin" + mp + " (" + m.pins[mp] + ")"));
    }
    const nl = edgesNow.findIndex((ed) => !ed.via);
    if (nl >= 0) { select({kind: "edge", n: nl}); ok("a direct connection says so", $("details").textContent.includes("direct (no logic")); }
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
      // names: what the driver connects to by default, its own ports on hover; the context menu switches
      const an0 = [...bd.design.values()][0];
      const texts = () => [...$("svg").querySelectorAll("text")].map((t) => t.firstChild && t.firstChild.textContent);
      ok("a driver names what it connects to, its own port on hover",
         texts().includes(an0.label) && [...$("svg").querySelectorAll("text title")].some((t) => t.textContent.includes(bd.via + " port " + an0.own)));
      const box = document.querySelector("[data-sel='" + JSON.stringify({kind: "driver", use: bd.use, via: bd.via}) + "']").getBoundingClientRect();
      $("svg").dispatchEvent(new MouseEvent("contextmenu", {bubbles: true, cancelable: true, clientX: box.left + 5, clientY: box.top + 5}));
      const items = [...document.querySelectorAll("#picker.menu .picker-item")].map((b) => b.textContent);
      ok("right-clicking a driver offers its naming and its Verilog",
         items.some((t) => t.includes("its own port names")) && items.includes("Show Verilog source"));
      const saved = JSON.stringify(driverNamings());
      [...document.querySelectorAll("#picker.menu .picker-item")].find((b) => b.textContent.includes("its own port names")).click();
      const own = drivers().find((x) => x.via === bd.via);
      ok("…and switching shows the driver's own port names", !$("picker") && texts().includes([...own.design.values()][0].own));
      // on both sides: a driver whose pin-side ports are named otherwise than the part's pins
      const other = drivers().flatMap((x) => [...x.pin.values()].map((an) => [x, an])).find(([, an]) => an.own !== an.label);
      if (other) {
        setDriverNaming(other[0].via, "own");
        ok("the pin side shows the driver's own names too (" + other[1].label + " → " + other[1].own + ")", texts().includes(other[1].own));
      }
      S.driverNames = JSON.parse(saved);
      try { localStorage.setItem("unifpga.driverNames", saved); } catch (e) { /* private window */ }
      render();
    }
    // the splitter resizes the side panel
    {
      const side = $("side"), sp = $("splitter"), w0 = side.getBoundingClientRect().width, r = sp.getBoundingClientRect();
      const pe = (type, x) => sp.dispatchEvent(new PointerEvent(type, {clientX: x, clientY: r.top + 20, pointerId: 7, bubbles: true}));
      pe("pointerdown", r.left + 4); pe("pointermove", r.left - 96); pe("pointerup", r.left - 96);
      ok("dragging the splitter widens the side panel", Math.abs(side.getBoundingClientRect().width - (w0 + 100)) < 3);
      pe("pointerdown", r.left - 96); pe("pointerup", r.left + 4);
    }
    // double click: the Verilog that defines it, scrolled to the marked line
    const dbl = async (sel) => {
      const el = document.querySelector("[data-sel='" + JSON.stringify(sel) + "']");
      const b = el.getBoundingClientRect();
      // two clicks through the page's own handler, as a browser sends them; the
      // first selects and redraws the drawing, the second lands on a new element
      const at = {clientX: b.left + Math.min(b.width / 2, 20), clientY: b.top + Math.min(b.height / 2, 6), bubbles: true, detail: 1};
      S.lastClick = null;
      $("svg").dispatchEvent(new MouseEvent("click", at));
      const under = document.elementFromPoint(at.clientX, at.clientY);
      $("svg").dispatchEvent(new MouseEvent("click", Object.assign({}, at, {detail: 2})));
      S.dblTargetReplaced = !el.isConnected || under !== el;
      for (let k = 0; k < 100 && !(S.srcView && !$("src-title").textContent.startsWith("generating")); k++) await new Promise((r) => setTimeout(r, 30));
      await new Promise((r) => setTimeout(r, 0));
    };
    const inView = (n) => { const box = $("src-code"), e = box.querySelector('[data-line="' + n + '"]');
      return !!e && e.offsetTop >= box.scrollTop && e.offsetTop + e.offsetHeight <= box.scrollTop + box.clientHeight; };
    if (drs.length) {
      const d0 = drs[0];
      S.srcView = null;
      await dbl({kind: "driver", use: d0.use, via: d0.via});
      const v = S.srcView;
      const opened = !$("side-source").hidden && v && v.file.path && v.file.text.split("\n")[v.line - 1].includes("module " + d0.via);
      ok("double-clicking a driver opens its module in the Source tab" + (opened ? "" : " [" + JSON.stringify({via: d0.via,
         path: v && v.file.path, line: v && v.line, title: $("src-title").textContent, status: $("status").textContent}) + "]"), opened);
      ok("a double click works although the first click redrew the drawing", S.dblTargetReplaced === true && opened);
      ok("the definition is scrolled into view and marked", inView(v.line) && $("src-code").querySelector('[data-line="' + v.line + '"]').classList.contains("focus"));
      ok("the code is coloured", !!$("src-code").querySelector(".k") && !!$("src-code").querySelector(".c, .n"));
      const top = S.lastFiles[0];
      ok("the rig's top marks the driver's section", top.path === null && top.highlight.some((n) => top.text.split("\n")[n - 1].includes(d0.via)));
      // navigation: a module name in the top opens that module; a signal jumps to its declaration
      const tabsBefore = S.tabs.length;
      openView(top, top.highlight[0], top.highlight, "test");
      ok("opening another file adds a tab and keeps the first", S.tabs.length === tabsBefore + 1 && S.tabs.some((x) => x.path && x.text.includes("module " + d0.via)));
      await new Promise((r) => setTimeout(r, 0));
      const mtok = [...$("src-code").querySelectorAll(".m")].find((e) => e.textContent === d0.via);
      if (mtok) { mtok.click(); for (let k = 0; k < 100 && S.srcView.file === top; k++) await new Promise((r) => setTimeout(r, 30));
        ok("clicking a module name opens its file", S.srcView.file.path && S.srcView.file.text.includes("module " + d0.via)); }
      historyStep(-1);
      ok("back returns to the top", tabKey(S.srcView.file) === "top.sv");
      const nav = [...$("src-code").querySelectorAll(".id.nav")].find((e) => top.decl.get(e.textContent) !== Number(e.closest(".ln").dataset.line));
      const topTab = S.srcView.file;
      if (nav) { nav.click(); ok("clicking a signal jumps to its declaration", S.srcView.line === topTab.decl.get(nav.textContent)); }
      const n0 = S.tabs.length;
      closeTab(topTab);
      ok("a tab closes with its ×, the others stay", S.tabs.length === n0 - 1 && !S.tabs.includes(topTab) && S.srcView && S.srcView.file !== topTab);
      showSide("props");
    }
    // a module whose exclusive capability the rig already has: marked in the
    // selector, and Auto-wire says it will not reach the design
    {
      const taken = new Set();
      S.setup.use.forEach((u, i) => { for (const c of providedCaps(usePeripheral(u))) if (capAggregation(c) === "exclusive" && !profileDrop(i)) taken.add(c); });
      const inRig = S.board.modules.find((m) => S.setup.use.some((u) => u.module === m.id));
      if (inRig) ok("the module selector marks a module already in the rig",
                    [...$("add-module").options].some((o) => o.value === inRig.id && o.textContent.includes("in the rig")));
      const clash = S.board.modules.find((m) => providedCaps(m.peripheral).some((c) => taken.has(c)));
      if (clash) {
        ok("the selector says a new one would not reach the design",
           [...$("add-module").options].some((o) => o.value === clash.id && o.textContent.includes("would not reach the design")));
        const n0 = S.setup.use.length;
        S.setup.use.push({module: clash.id, wires: {}}); await changed("add clash"); await settle();
        await autoWire(n0); await settle();
        const noRoom = /free pins/.test($("status").textContent);          // nowhere to wire it: that is the message
        ok("Auto-wire says why the part does not reach the design", /does not reach the design|only in part/.test($("status").textContent) || noRoom);
        ok("the part is marked in the drawing", $("svg").textContent.includes(noRoom ? "not wired" : "capability already provided"));
        S.setup.use.splice(n0, 1); S.sel = null; await changed("remove clash"); await settle();
      }
    }
    {
      S.lastClick = null;
      const t0 = Date.now, fake = [0, DOUBLE_MS + 50];
      Date.now = () => fake.shift();
      const one = isDoubleClick({clientX: 5, clientY: 5}), two = isDoubleClick({clientX: 5, clientY: 5});
      Date.now = t0;
      ok("two clicks further apart than a double click stay single clicks", !one && !two);
      S.lastClick = null;
    }
    // every kind of clickable thing in the drawing opens Verilog with marked lines
    {
      const seen = new Map();
      for (const e of document.querySelectorAll("#svg [data-sel]")) {
        const s = JSON.parse(e.getAttribute("data-sel"));
        if (!seen.has(s.kind)) seen.set(s.kind, s);
      }
      const bad = [];
      for (const [kind, s] of seen) {
        S.srcView = null;
        try { await openVerilog(s); } catch (x) { bad.push(kind + ": " + x.message); continue; }
        const v = S.srcView;
        if (!v) bad.push(kind + ": nothing opened");
        else if (!v.marks.length && !/not used by this rig/.test(v.file.note || ""))
          bad.push(kind + ": no marked line in " + (v.file.path || "top.sv") + " for " + JSON.stringify(verilogTarget(s)));
      }
      ok("double-clicking any kind of thing (" + [...seen.keys()].join(", ") + ") opens marked Verilog" + (bad.length ? " [" + bad.join("; ") + "]" : ""), !bad.length);
      showSide("props");
    }
    // shared pins and bits are marked and explained
    {
      const sr = sharedRefs();
      if (sr.size) {
        const ref = [...sr.keys()][0], key = refIndex()[ref];
        ok("a pin several parts reach is warned about", (S.ev.problems || []).some((q) => q.level === "warning" && q.message.includes(ref)));
        S.sel = null; render();
        ok("the design bits on it are outlined as conflicted", [...document.querySelectorAll('#svg rect[stroke="#f76707"] title')].some((x) => x.textContent.includes("conflicted")));
        if (key) { select({kind: "pin", conn: key.split(".")[0], key: key.split(".")[1]});
                   ok("its panel says it is shared", $("details").textContent.includes("This pin is shared")); }
      }
    }
    // the toolchains / chips a rig is checked with: the default fixed, the others toggled
    if (!STATIC && S.board.toolchains.length > 1) {
      const saved = clone(S.setup);
      showTab("config");
      const boxes = [...document.querySelectorAll('#targets input[data-target^="toolchains:"]')];
      ok("the rig lists every toolchain of its board to be checked with", boxes.length === S.board.toolchains.length);
      const def = boxes.find((b) => b.dataset.target === "toolchains:" + S.setup.toolchain);
      ok("its default toolchain is checked and fixed", def && def.checked && def.disabled);
      const other = boxes.find((b) => b !== def);
      const was = (S.setup.toolchains || [S.setup.toolchain]).includes(other.dataset.target.split(":")[1]);
      other.click(); await changedDone();
      const now = (S.setup.toolchains || [S.setup.toolchain]).includes(other.dataset.target.split(":")[1]);
      ok("a toolchain checkbox adds or removes it", now === !was && (S.setup.toolchains || [S.setup.toolchain])[0] === S.setup.toolchain);
      ok("the configuration text follows", (S.ev.configuration_text || "").includes("toolchains:") === (S.setup.toolchains || []).length > 1);
      S.setup = saved; S.dirty = false; await changed("restored"); S.dirty = false;
      if ((S.board.parts || []).length > 1)
        ok("a board with several chips offers them", !$("part-label").hidden && [...$("part").options].length >= S.board.parts.length);
      showTab("rig");
    }
    // New setup: an inline form, checked as you type; Create makes an unsaved rig
    if (!STATIC) {
      const saved = clone(S.setup), options = [...$("setup").options].length;
      showTab("config");                       // where the button is
      $("new-setup").click();
      ok("New setup opens an inline form", !$("new-setup-form").hidden && document.activeElement === $("ns-id"));
      ok("the form offers the board's toolchains only", [...$("ns-toolchain").options].map((o) => o.value).join() === S.board.toolchains.join());
      $("ns-id").value = saved.id; $("ns-id").dispatchEvent(new Event("input"));
      ok("an existing name is refused as you type", $("ns-create").disabled && $("ns-msg").textContent.includes("exists already"));
      $("ns-id").value = "Bad Name"; $("ns-id").dispatchEvent(new Event("input"));
      ok("a malformed name is refused", $("ns-create").disabled);
      $("ns-id").value = "selftest_new_rig"; $("ns-id").dispatchEvent(new Event("input"));
      $("new-setup-form").requestSubmit();
      await settle();
      ok("Create makes an empty rig with the board's clock, unsaved", S.setup.id === "selftest_new_rig" && $("new-setup-form").hidden &&
         S.setup.use.length >= 1 && S.setup.use.every((u) => u.onboard));
      [...$("setup").options].filter((o) => o.value === "selftest_new_rig").forEach((o) => o.remove());
      S.setup = saved; $("setup").value = saved.id; S.sel = null; await changed("restored"); await settle();
      ok("the setup list is as it was", [...$("setup").options].length === options);
      showTab("rig");
    }
    // a conflict offers buttons that resolve it; pressing one re-evaluates without it
    {
      const p = (S.ev.problems || []).find((q) => (q.resolve || []).length);
      if (p) {
        const saved = clone(S.setup);
        ok("a conflict in Problems has resolve buttons", [...$("problems").querySelectorAll(".fixes button")].some((b) => b.textContent === p.resolve[0].label));
        select({kind: "use", use: p.uses[0]});
        ok("the parts' panels show the conflict with its buttons", $("details").textContent.includes("Conflict: ") &&
           [...$("details").querySelectorAll(".fixes button")].length === p.resolve.length);
        const fix = p.resolve.find((f) => f.op === "autowire") || p.resolve[0];
        const ev0 = S.ev;
        [...$("problems").querySelectorAll(".fixes button")].find((b) => b.textContent === fix.label).click();
        for (let n = 0; S.ev === ev0 && n < 200; n++) await new Promise((r) => setTimeout(r, 50));   // auto-wiring asks the server first
        await settle();
        ok("pressing '" + fix.label + "' resolves the conflict", !(S.ev.problems || []).some((q) => q.message === p.message));
        S.setup = saved; S.sel = null; await changed("restored"); await settle();
      }
      const sp = ports().find((q) => sharedBits(q).size);
      if (sp) {
        const b = [...sharedBits(sp).keys()][0];
        select({kind: "vbit", cap: sp.capability, signal: sp.signal, bit: b});
        ok("a bit several parts feed says how they combine", $("details").textContent.includes(sp.direction === "hw_to_user" ? " OR " : "drives all of"));
      }
    }
    // widths the rig sets are shown as design_top parameters
    const pp = ports().find((q) => q.width_parameter && q.providers.length);
    if (pp) {
      ok("a parameterized port shows its parameter", $("svg").textContent.includes(widthText(pp)));
      select({kind: "vport", cap: pp.capability, signal: pp.signal});
      ok("its panel says the width is a design_top parameter set by the rig", $("details").textContent.includes("a design_top parameter"));
    }
    for (const q of ports().filter((q) => capDef(q.capability).summary && q.providers.length).slice(0, 1))
      ok("a parameterized capability's variant is named", $("svg").textContent.includes(capSummary(q.capability)));
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
    S.setup.id = "selftest_rig"; delete S.setup.notes; delete S.setup.aliases;  // a fresh rig: no profile, no old ids
    await changed("fresh id"); await settle();
    // a module this rig can take: the first the server can wire, whose capabilities are not taken
    const taken = new Set();
    S.setup.use.forEach((u) => { for (const c of providedCaps(usePeripheral(u))) if (capAggregation(c) === "exclusive") taken.add(c); });
    let mod = null, suggestion = null;
    for (const m of S.board.modules) {
      if (providedCaps(m.peripheral).some((c) => taken.has(c))) continue;
      const trial = clone(S.setup);
      trial.use.push({module: m.id, wires: {}});
      try { suggestion = await api("/api/autowire", {setup: trial, use: trial.use.length - 1}); mod = m; break; } catch (e) { /* no room for it */ }
    }
    const n = S.setup.use.length;
    if (!mod) log.push("SKIP adding and wiring a module: no module fits the free header pins of this rig");
    else {
      const signalPins = Object.keys(mod.pins).filter((p) => !passive(mod.pins[p]));
      const where = wiresOf(Object.assign({module: mod.id}, suggestion));
      const first = signalPins[0];
      S.setup.use.push({module: mod.id, wires: {[first]: (S.board.connectors[0] ? S.board.connectors[0].id : "nowhere") + ".99"}});
      await changed("broken"); await settle();
      ok("a broken module leaves the rest traced", !!S.ev.trace && S.ev.excluded.length === 1 && ports().length > 0);
      ok("the untraced module is named in Problems", $("problems").textContent.includes("not traced"));
      await autoWire(S.setup.use.length - 1); await settle();
      const aw = S.setup.use[S.setup.use.length - 1];
      ok("auto-wire wires every signal pin", Object.keys(wiresOf(aw)).length === signalPins.length &&
         !S.ev.excluded.length && !(S.ev.problems || []).some((p) => p.level === "error"));
      S.setup.use.pop(); await changed("undo"); await settle();
      const addSel = $("add-module");
      addSel.value = mod.id; addSel.dispatchEvent(new Event("change")); await settle(); await settle();
      showTab("rig");
      ok("added module is listed", S.setup.use.length === n + 1);
      ok("unwired module reports its required signals", errors().some((m) => m.includes("required signal")));
      const i = S.setup.use.length - 1;
      // the pins the server calls free, wired by clicking module pin then header pin
      for (const p of signalPins) { const [c, k] = where[p].split("."); modulePinClicked(i, p); pinClicked(c, k); await settle(); }
      ok("wired by clicking module pin then header pin", Object.keys(S.setup.use[i].wires).length === signalPins.length);
      ok("the added module reaches the design", errors().length === 0 && portsOfUse(i).length > 0 && !partStatus(i));
      const [c0, k0] = where[first].split(".");
      pinClicked(c0, k0); await settle();
      ok("selecting a header pin shows its wire", $("details").textContent.includes("pin " + first));
      delete S.setup.use[i].wires[first]; await changed("disconnect"); await settle();
      const sigDef = (S.board.peripherals[mod.peripheral].signals || []).find((s) => s.name === mod.pins[first]);
      if (sigDef && !sigDef.optional)
        ok("disconnecting brings back the required-signal error", errors().some((m) => m.includes("required signal '" + mod.pins[first] + "'")));
      select({kind: "use", use: i});
      ok("the header's Remove names the selected module", !$("remove-sel").disabled && $("remove-sel").textContent.includes(useLabel(S.setup.use[i])));
      $("remove-sel").click(); await settle();
      ok("removing the module restores the rig", S.setup.use.length === n && $("remove-sel").disabled);
    }
    // an on-board part that provides no capability a rig needs (not the clock)
    const needed = (S.board.capabilities || []).filter((c) => c.required).map((c) => c.id);
    const ob = S.board.onboard.find((o) => S.setup.use.some((u) => u.onboard === o.id &&
                                      !providedCaps(usePeripheral(u)).some((c) => needed.includes(c))));
    const oi = S.setup.use.findIndex((u) => u.onboard === ob.id);
    S.setup.use.splice(oi, 1); await changed("unuse"); await settle();
    ok("an on-board device can be dropped", !S.setup.use.some((u) => u.onboard === ob.id));
    const r = await api("/api/save", {setup: S.setup});
    ok("saved", r.configuration.endsWith("selftest_rig.yml"));
    S.dirty = false;
    const fitting = Object.entries(S.ev.designs || {}).find(([, unmet]) => !unmet.length);
    const pr = await api("/api/project", {setup_id: "selftest_rig", design: fitting[0]});
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
  try {
    showTab("designs");
    for (let k = 0; k < 600 && !S.dt; k++) await new Promise((r) => setTimeout(r, 50));
    ok("the Designs page lists every design", !!S.dt && document.querySelectorAll(".d-row").length === S.dt.designs.length);
    ok("it checks every configuration, each with its board", !!S.dt && S.dt.configurations.length > 0 &&
       S.dt.configurations.every((c) => c.board && c.board_name));
    const any = S.dt.designs.find((d) => d.fits.some((k) => S.dt.configurations[k].setup));
    $("d-search").value = any.id; renderDesigns();
    ok("searching narrows the list", [...document.querySelectorAll(".d-row")].every((r) => r.dataset.design.includes(any.id) || S.dt.designs.find((d) => d.id === r.dataset.design).requires.join(" ").includes(any.id)));
    document.querySelector('.d-row[data-design="' + any.id + '"]').click();
    ok("a design shows every board's configurations with fit or reason", document.querySelectorAll("#d-detail .cfg").length === S.dt.configurations.length);
    const k = any.fits.find((j) => S.dt.configurations[j].setup), c = S.dt.configurations[k];
    await openRig(c, any.id);
    ok("Open rig opens that configuration's rig with the design chosen", !$("tab-rig").hidden && S.setup.id === c.id && S.design === any.id);
    $("d-search").value = ""; S.dsel = null;
    // the chosen design is the Source panel's pinned, editable tab
    for (let k = 0; k < 200 && !(S.tabs || []).some((x) => x.pinned && x.design === any.id); k++) await new Promise((r) => setTimeout(r, 50));
    const pin = (S.tabs || []).find((x) => x.pinned);
    ok("the chosen design is pinned in Source", !!pin && pin.design === any.id && pin.path === "designs/" + any.id + "/design_top.sv" &&
       S.srcView && S.srcView.file === pin);
    ok("the pinned design has no close button", !!pin && !$("src-files").querySelector(".src-tab.pinned .x"));
    if (pin) {
      const original = pin.text;
      [...$("src-edit").querySelectorAll("button")].find((b) => b.textContent === "Edit").click();
      const ta = $("src-code").querySelector("textarea");
      ok("Edit turns it into an editor", !!ta && ta.value === original);
      const layer = $("src-code").querySelector(".ed-layer");
      {
        // ⌘/Ctrl-click navigation inside the editor: a name declared in the file moves the caret to it
        const decl = [...declarations(ta.value)].find(([, l]) => l > 20);
        if (decl) {
          const [name, dl] = decl, idx = ta.value.lastIndexOf(name);
          ta.selectionStart = ta.selectionEnd = idx + 1; goToDefinition(pin, ta);
          ok("F12 / " + MODS.points + "-click in the editor goes to a definition",
             ta.value.slice(0, ta.selectionStart).split("\n").length === dl);
        }
      }
      ok("the editor stays coloured", !!layer && layer.querySelectorAll(".k").length > 0 && layer.textContent.startsWith(original) &&
         $("src-code").querySelector(".ed-gutter").textContent.split("\n").length >= original.split("\n").length);
      ta.value = original + "\n// edited by the self-test\n"; ta.dispatchEvent(new Event("input"));
      ok("an edit marks it unsaved", dirty(pin) && $("src-files").textContent.includes("●") && !$("src-edit").querySelector("button:nth-child(2)").disabled);
      await saveDesign(pin);
      const back = await api("/api/module", {name: "design_top", design: any.id});
      ok("Save writes the design file", back.text === original + "\n// edited by the self-test\n" && !dirty(pin));
      const stale = await api("/api/design/save", {design: any.id, text: "x", loaded: original}).then(() => null, (e) => e.message);
      ok("a save over a file changed since loading is refused", !!stale && stale.includes("changed on disk"));
      pin.editing = true; pin.draft = original; await saveDesign(pin);
      ok("saving the original text restores it", (await api("/api/module", {name: "design_top", design: any.id})).text === original);
      // another design replaces the pinned tab
      const other = S.board.designs.find((d) => d !== any.id && !((S.ev.designs || {})[d] || []).length);
      if (other) {
        S.design = other; designChoices();
        for (let k = 0; k < 200 && !(S.tabs || []).some((x) => x.pinned && x.design === other); k++) await new Promise((r) => setTimeout(r, 50));
        ok("choosing another design replaces the pinned tab", S.tabs.filter((x) => x.pinned).length === 1 && S.tabs.find((x) => x.pinned).design === other &&
           !S.tabs.some((x) => x.path === pin.path));
      }
    }
  } catch (e) { ok("designs page: " + e.message, false); }
  document.body.append(h("pre", {id: "selftest"}, log.join("\n")));
}

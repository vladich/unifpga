"""
The board editor: a local web page (./unifpga serve) that draws a setup —
the virtual device design_top sees, the board with its on-board devices and
connectors, the add-on modules and their wires — and edits it: add, rewire and
remove modules, use on-board devices or not, hand connectors to the design as
gpio, set parameters. Every edit is evaluated by the same code the build uses
(tools/setup.py generate + validate, config/init.py resolve, tools/trace.py),
so what the page shows is what synthesis will get. Saving writes
config/setups/<id>.yml and the configuration it generates; "project" writes
the SystemVerilog project for a design (the dry run of synthesize.py into
designs/<design>/run/<id>/).

The page itself is tools/studio/ (index.html, studio.js, studio.css); the
server only answers JSON. It listens on 127.0.0.1 and refuses a write that
does not carry the page's own header and a local Origin, so another web site
open in the same browser cannot drive it.
"""

import io
import json
import os
import re
import threading
import zipfile

from config import init as config_init
from tools import codegen
from tools import setup as su
from tools import trace as tr

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(REPO, "tools", "studio")
DESIGNS_DIR = os.path.join(REPO, "designs")
ID_RE = re.compile(r"^[a-z0-9_]{1,80}$")
WRITE_HEADER = "X-Unifpga-Studio"


def _code_fingerprint():
    """Modification times of the Python code the server runs; the page is
    told when they change under a running server (restart it)."""
    out = []
    for sub in ("tools", "config"):
        base = os.path.join(REPO, sub)
        for name in sorted(os.listdir(base)):
            if name.endswith(".py"):
                out.append(os.stat(os.path.join(base, name)).st_mtime_ns)
    return tuple(out)


_STARTED_WITH = _code_fingerprint()


class ApiError(Exception):
    def __init__(self, status, message):
        Exception.__init__(self, message)
        self.status = status


# ---------------------------------------------------------------------------
# data for the page
# ---------------------------------------------------------------------------

def list_designs():
    if not os.path.isdir(DESIGNS_DIR):
        return []
    return sorted(d for d in os.listdir(DESIGNS_DIR)
                  if os.path.isfile(os.path.join(DESIGNS_DIR, d, "design_top.sv")))


def board_toolchains(board_id):
    """The toolchains that can build for the board, sorted."""
    boards, chips = config_init._board_index(), config_init.read_chips()
    out = []
    for tc in sorted(config_init.read_toolchains()):
        try:
            if config_init.is_compatible(boards, chips, board_id, tc):
                out.append(tc)
        except config_init.ConfigError:
            pass
    return out


def board_data(board_id):
    """Everything the page needs to draw and edit rigs on one board."""
    layouts = su.read_layouts()
    if board_id not in layouts:
        raise ApiError(404, "no layout for board '{}'".format(board_id))
    layout = layouts[board_id]
    pinmap = config_init.read_board_pinmap(board_id) or {}
    ctypes = su.read_connectors()

    def pins(ref):
        return [{"ref": bit, "pin": pin} for bit, pin in codegen._bind_pins(pinmap, ref)]

    connectors = []
    for c in layout.get("connectors") or []:
        ctype = ctypes.get(c["type"]) or {}
        rows = [[str(k) for k in r] for r in ctype.get("rows") or []]
        keys = [str(k) for k in c.get("pins") or {}]
        if not rows:
            half = (len(keys) + 1) // 2
            rows = [keys[:half], keys[half:]]
        connectors.append({
            "id": c["id"], "label": c.get("label") or c["id"], "type": c["type"], "bank": c.get("bank"),
            "voltage": ctype.get("voltage"), "rows": rows,
            "power": {str(k): v for k, v in (ctype.get("power") or {}).items()},
            "pins": {str(k): {"ref": ref, "pin": ", ".join(p["pin"] or "?" for p in pins(ref))}
                     for k, ref in (c.get("pins") or {}).items()},
        })
    onboard = []
    for o in layout.get("onboard") or []:
        variants = [{"id": vid, "label": label, "attach": attach,
                     "pins": {s: pins(ref) for s, ref in (attach.get("bind") or {}).items()}}
                    for vid, label, attach in su.onboard_variants(o)]
        if not variants:
            # a device with no peripheral model yet: drawn with its pins, not usable
            dev = o.get("device") or {}
            bank = ((pinmap.get("pinBanks") or {}).get(dev.get("bank")) or {})
            bpins = bank.get("pins") if isinstance(bank, dict) else bank
            refs = ({s: "{}.{}".format(dev["bank"], s) for s in bpins} if isinstance(bpins, dict)
                    else {dev.get("kind", "pins"): dev.get("bank")})
            onboard.append({"id": o["id"], "label": o.get("label") or o["id"], "variants": [], "attach": None,
                            "device": {"kind": dev.get("kind"), "bank": dev.get("bank")},
                            "pins": {s: pins(ref) for s, ref in refs.items()}})
            continue
        # attach / pins: the first variant's, for an older page
        onboard.append({"id": o["id"], "label": o.get("label") or o["id"], "variants": variants,
                        "attach": variants[0]["attach"], "pins": variants[0]["pins"]})
    peripherals = config_init.read_peripherals()
    modules = su.read_modules()
    used = {m["peripheral"] for m in modules.values()} | {su.gpio_passthrough()[0]} | \
           {attach["peripheral"] for o in layout.get("onboard") or [] for _v, _l, attach in su.onboard_variants(o)}
    return {
        "board": board_id,
        "verified": bool(layout.get("verified")),
        "connectors": connectors,
        "onboard": onboard,
        "modules": sorted(modules.values(), key=lambda m: m["id"]),
        "peripherals": {pid: {"description": p.get("description"), "signals": p.get("signals") or [],
                              "parameters": p.get("parameters") or {}, "provides": p.get("provides") or [],
                              "driver": (p.get("driver") or {}).get("module") if p.get("driver") else None,
                              "driver_file": (p.get("driver") or {}).get("file") if p.get("driver") else None}
                        for pid, p in peripherals.items() if pid in used},
        "setups": sorted(s for s, v in su.read_setups().items() if v["board"] == board_id),
        "capabilities": [{"id": cid, "aggregation": c.get("aggregation"),
                          "signals": [{"name": s["name"], "direction": s.get("direction")}
                                      for s in c.get("signals") or []],
                          # what the editor says about it: every rig needs one (clock), the
                          # peripheral `gpio:` attaches, a summary line of its design_top
                          # parameters, their names, and the WxH its requirements compare
                          "required": bool(c.get("required")), "passthrough": c.get("passthrough"),
                          "summary": c.get("summary"), "size": c.get("size"), "depth": c.get("depth"),
                          "design_parameters": list(((c.get("design") or {}).get("parameters") or {}))}
                         for cid, c in config_init.read_capabilities().items()],
        # only the toolchains the board's chip(s) support (the chip registry's
        # Toolchains, inherited from the family's DefaultToolchains)
        "toolchains": board_toolchains(board_id),
        "designs": list_designs(),
    }


_REQUIREMENTS = {}


def design_requirements(design):
    """The design's `// requires:` block, parsed (cached on the file's mtime)."""
    from tools import design_requirements as dr
    path = os.path.join(DESIGNS_DIR, design, "design_top.sv")
    key = os.stat(path).st_mtime_ns
    cached = _REQUIREMENTS.get(path)
    if cached is None or cached[0] != key:
        cached = (key, dr.parse(path))
        _REQUIREMENTS[path] = cached
    return cached[1]


def requires_lines(design):
    """The lines of a design's `// requires:` block, as written."""
    with open(os.path.join(DESIGNS_DIR, design, "design_top.sv"), encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    out, inside = [], False
    for line in lines:
        if not inside:
            inside = bool(re.match(r"^\s*//\s*requires\s*:\s*$", line, re.I))
            continue
        if not re.match(r"^\s*//", line):
            break
        text = re.sub(r"^\s*//\s*", "", line).strip()
        if text:
            out.append(text)
    return out


_TABLE = {"key": None, "data": None}
_TABLE_LOCK = threading.Lock()


def _table_key():
    """Changes whenever a configuration, profile, peripheral, capability,
    board or design file does."""
    newest, count = 0, 0
    for root in (os.path.join(REPO, "config"), DESIGNS_DIR):
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                if name.endswith((".yml", ".yaml", ".sv")):
                    count += 1
                    newest = max(newest, os.stat(os.path.join(dirpath, name)).st_mtime_ns)
    return count, newest


def design_table():
    """Every design against every configuration in the repository:
    {configurations: [{id, board, board_name, toolchain, setup, layout, error?}],
     designs: [{id, requires: [line], fits: [configuration index], unmet: {index: [reason]}}]}.
    Computed once and kept until a file it depends on changes."""
    from tools import design_requirements as dr
    with _TABLE_LOCK:
        key = _table_key()
        if _TABLE["key"] == key:
            return _TABLE["data"]
        cfgs = config_init.read_configurations()
        setups, layouts = su.read_setups(), su.read_layouts()
        caps = config_init.read_capabilities()
        configurations, resolved = [], []
        for cid in sorted(cfgs):
            c = cfgs[cid]
            entry = {"id": cid, "board": c["board"], "board_name": c["board"], "toolchain": c.get("toolchain"),
                     "setup": cid in setups, "layout": c["board"] in layouts}
            try:
                r = config_init.resolve_configuration(cid)
                entry["board_name"] = r["board"].get("BoardName") or c["board"]
                resolved.append((r, dr.design_parameters(r)))
            except (config_init.ConfigError, codegen.CodegenError, KeyError, ValueError) as exc:
                entry["error"] = str(exc).splitlines()[0]
                resolved.append(None)
            configurations.append(entry)
        designs = []
        for d in list_designs():
            reqs = design_requirements(d)
            unmet = {}
            for k, x in enumerate(resolved):
                if x is None:
                    unmet[k] = ["the configuration cannot be resolved: " + configurations[k]["error"]]
                elif reqs:
                    u = dr.check(x[0], reqs, caps, x[1])
                    if u:
                        unmet[k] = u
            designs.append({"id": d, "requires": requires_lines(d),
                            "fits": [k for k in range(len(resolved)) if k not in unmet],
                            "unmet": {str(k): v for k, v in unmet.items()}})
        _TABLE.update(key=key, data={"configurations": configurations, "designs": designs})
        return _TABLE["data"]


def design_fit(resolved):
    """{design: [unmet requirement, ...]} for every design; [] = it fits."""
    from tools import design_requirements as dr
    capabilities = config_init.read_capabilities()
    parameters = dr.design_parameters(resolved)
    return {d: dr.check(resolved, design_requirements(d), capabilities, parameters) if design_requirements(d) else []
            for d in list_designs()}


def _traceable(setup):
    """(setup, [original use index per kept use], [{use, label, reason}]):
    the setup without the uses that cannot be generated or traced, so the
    virtual device can be shown for the rest while problems are being fixed."""
    uses = setup.get("use") or []
    kept, excluded = [], []
    for k, use in enumerate(uses):
        try:
            su.generate(dict(setup, use=[uses[j] for j in kept] + [use]))
            kept.append(k)
        except su.SetupError as exc:
            excluded.append({"use": k, "label": su.use_label(use, use.get("raw") or {}), "reason": str(exc)})
    return kept, excluded


def _trace(setup, kept):
    cfg = su.generate(dict(setup, use=[setup["use"][k] for k in kept]))
    resolved = config_init.resolve_configuration(setup["id"], configuration=cfg)
    result = tr.trace(resolved)
    # attach indices of the reduced setup -> use indices of the whole one
    for a in result["attaches"]:
        if a["attach_index"] is not None:
            a["attach_index"] = kept[a["attach_index"]]
    for p in result["ports"]:
        for pr in p["providers"]:
            if pr["attach_index"] is not None:
                pr["attach_index"] = kept[pr["attach_index"]]
    return resolved, result


def evaluate(setup):
    """What the build makes of a (possibly unsaved) setup: problems, the
    configuration text, the virtual device traced to pins (for as much of the
    setup as can be traced: uses that break it are listed in `excluded`), and
    which designs fit."""
    out = {"problems": [], "configuration_text": None, "trace": None, "profile": None, "designs": None, "parts": [],
           "excluded": [], "server_stale": _code_fingerprint() != _STARTED_WITH}
    from config import profile
    prof = profile.load(setup.get("id")) if profile.enabled() else None
    if prof:
        out["profile"] = os.path.relpath(profile.path_for(setup["id"]), REPO)
    out["profile_drops"] = []
    try:
        cfg = su.generate(setup)
        out["profile_drops"] = profile_drops(cfg, prof)
        out["configuration_text"] = su.emit_configuration(cfg, setup.get("notes"))
        clashes = []
        out["problems"] = [{"level": level, "message": msg} for level, msg in su.validate(setup, clashes)]
        fixes = _Fixes(setup)
        for a, b, pins in clashes:
            msg = next(p for p in out["problems"] if p["message"].startswith(
                ("pin " if len(pins) == 1 else "pins ") + ", ".join(pins) + " used by both"))
            msg.update(uses=[a, b], resolve=fixes.either(a, b))
    except su.SetupError as exc:
        out["problems"] = [{"level": "error", "message": str(exc)}]
    kept, excluded = _traceable(setup)
    for _attempt in range(len(kept) + 1):
        try:
            resolved, out["trace"] = _trace(setup, kept)
            break
        except (config_init.ConfigError, codegen.CodegenError) as exc:
            error = str(exc)
            # set aside the latest use whose removal lets the rest trace
            for k in reversed(kept):
                try:
                    _trace(setup, [j for j in kept if j != k])
                except (config_init.ConfigError, codegen.CodegenError, su.SetupError):
                    continue
                use = setup["use"][k]
                excluded.append({"use": k, "label": su.use_label(use, use.get("raw") or {}), "reason": error})
                kept = [j for j in kept if j != k]
                break
            else:
                out["problems"].append({"level": "error", "message": error})
                break
    out["excluded"] = sorted(excluded, key=lambda x: x["use"])
    out["parts"] = part_status(setup, kept, out["excluded"], resolved if out["trace"] is not None else None,
                               out["trace"], out["profile"], out["profile_drops"])
    fixes = _Fixes(setup)
    out["problems"].extend(shared_pin_warnings(setup, out["trace"], fixes))
    for x in out["parts"]:
        for reason in x["reasons"]:
            if reason[0] == "exclusive":
                first, cid = reason[2]["with"], reason[2]["capability"]
                out["problems"].append({"level": "warning", "message": x["label"] + ": " + reason[1],
                                        "uses": [first, x["use"]], "resolve": fixes.choose(cid, first, x["use"])})
    if out["trace"] is not None and not any(p["level"] == "error" for p in out["problems"]) and not excluded:
        out["designs"] = design_fit(resolved)
    return out


class _Fixes:
    """The buttons a problem between two parts offers: re-wire a module to free
    pins (when auto-wiring finds some), or remove a part. Each is
    {label, op: autowire | remove, use}; the page applies it and re-evaluates."""

    def __init__(self, setup):
        self.setup = setup
        self.uses = setup.get("use") or []
        self._rewire = {}

    def name(self, k):
        u = self.uses[k] if k is not None and k < len(self.uses) else {}
        return su.use_label(u, u.get("raw") or {})

    def rewirable(self, k):
        if k not in self._rewire:
            ok = bool(self.uses[k].get("module"))
            if ok:
                try:
                    su.autowire(self.setup, k)
                except su.SetupError:
                    ok = False
            self._rewire[k] = ok
        return self._rewire[k]

    def either(self, *ks):
        """two parts on the same pins: move one elsewhere or remove one"""
        out = [{"label": "Re-wire {} to free pins".format(self.name(k)), "op": "autowire", "use": k}
               for k in ks if self.rewirable(k)]
        return out + [{"label": "Remove {}".format(self.name(k)), "op": "remove", "use": k} for k in ks]

    def choose(self, cid, first, other):
        """two parts providing a capability design_top has one of: keep either"""
        return [{"label": "Use {} for {} (remove {})".format(self.name(other), cid, self.name(first)),
                 "op": "remove", "use": first},
                {"label": "Keep {} (remove {})".format(self.name(first), self.name(other)),
                 "op": "remove", "use": other}]


def shared_pin_warnings(setup, trace, fixes=None):
    """A warning for every set of parts that reach the same FPGA pins (the
    design's gpio on a header that also carries a driver's pins, say): the
    generated top connects all of them, and whichever drives a pin while
    another does fights it. One warning per set of parts, naming the pins."""
    uses = setup.get("use") or []
    fixes = fixes or _Fixes(setup)
    by_ref = {}
    for e in (trace or {}).get("edges") or []:
        by_ref.setdefault(e["ref"], {}).setdefault(e["use"], e)
    groups = {}                               # the parts -> [(ref, {use: edge})]
    for ref, users in by_ref.items():
        if len(users) > 1:
            groups.setdefault(tuple(sorted(users, key=lambda k: -1 if k is None else k)), []).append((ref, users))
    out = []
    for ks, refs in groups.items():
        parts = []
        for k in ks:
            edges = [users[k] for _ref, users in refs]
            if edges[0]["via"]:
                what = "its {}, through the {} driver".format(", ".join(e["signal"] for e in edges), edges[0]["via"])
            else:
                what = "the design's {}, direct".format(", ".join(
                    e["design_port"] + ("[{}]".format(e["bit"]) if e["bit"] is not None else "") for e in edges))
            parts.append("{} ({})".format(fixes.name(k), what))
        pins = ", ".join("{} (FPGA {})".format(ref, next(iter(users.values()))["pin"] or "?") for ref, users in refs)
        many = len(refs) > 1
        out.append({"level": "warning", "uses": [k for k in ks if k is not None],
                    "resolve": fixes.either(*[k for k in ks if k is not None]),
                    "message": "{} {} {} shared by {}: the generated top connects all of them, so only one may "
                               "drive {} at a time".format("pins" if many else "pin", pins, "are" if many else "is",
                                                           " and ".join(parts), "each" if many else "it")})
    return out


def part_status(setup, kept, excluded, resolved, trace, profile_path, drops):
    """[{use, label, connected, reasons: [(kind, text)]}] for every part that
    does not reach the design, or reaches it only in part:
      untraced   the build cannot place it (a design-wiring profile without
                 an entry for it, or another error), so it is not traced
      exclusive  design_top has one of a capability (audio_in, screen, ...)
                 and an earlier part already provides it: codegen keeps the
                 first provider, this one's gets no design port
      unwired    a module with none of its signal pins wired
      nothing    it provides nothing design_top has
    Profile drops are reported by profile_drops()."""
    uses = setup.get("use") or []
    names = [su.use_label(u, u.get("raw") or {}) for u in uses]

    def label(k):
        """a part's name, numbered when the rig has several of its kind"""
        same = [j for j, n in enumerate(names) if n == names[k]]
        return names[k] + (" #{}".format(same.index(k) + 1) if len(same) > 1 else "")
    reasons = {}
    for x in excluded:
        m = re.search(r"lab_bits\.(\w+) is set on one provider", x["reason"])
        if m and profile_path:
            text = ("the rig's design-wiring profile {} says which design bits of {} each part takes, and this part "
                    "has no entry there, so the build cannot place it. Save the rig under a new id (no profile "
                    "applies to it) or add the part to the profile.").format(profile_path, m.group(1))
        else:
            text = "the build cannot place it: " + x["reason"]
        reasons.setdefault(x["use"], []).append(("untraced", text))
    if resolved is not None:
        caps = config_init.read_capabilities()
        first = {}
        for a in resolved["peripherals"]:
            if a.get("attach_index") is None:
                continue
            k = kept[a["attach_index"]]
            for entry in codegen._active_provides(a["peripheral"], a.get("params") or {}):
                cid = entry["capability"]
                if (caps.get(cid) or {}).get("aggregation") != "exclusive":
                    continue
                if cid not in first:
                    first[cid] = k
                elif first[cid] != k:
                    ports = [p for p, c, _s, _w in codegen.design_ports() if c == cid]
                    reasons.setdefault(k, []).append(("exclusive", (
                        "design_top has one {} ({}) and {} already provides it; the build keeps the first "
                        "provider, so this part's {} does not reach the design. Keep one of them.").format(
                            cid, ", ".join(ports), label(first[cid]), cid), {"with": first[cid], "capability": cid}))
    dropped = {d["use"] for d in drops or []}
    edges = {e["use"] for e in (trace or {}).get("edges") or []}
    provides = {pr["attach_index"] for p in (trace or {}).get("ports") or [] for pr in p["providers"]}
    out = []
    for k, u in enumerate(uses):
        if k in dropped:
            continue
        rs = reasons.get(k, [])
        connected = k in edges or k in provides
        if u.get("module") and not u.get("wires") and not u.get("plug"):
            connected = False
            rs = rs + [("unwired", "none of its pins are wired yet (wire them, or Auto-wire)")]
        elif not connected and not rs and trace is not None and k in kept:
            rs = [("nothing", "it provides nothing design_top has a port for")]
        if rs:
            out.append({"use": k, "label": label(k), "connected": connected, "reasons": rs})
    return out


def profile_drops(cfg, prof):
    """[{use, peripheral, ties}]: the uses a design-wiring profile leaves out of
    the design (drop: true, matched as config/profile.py applies it: the n-th
    attach of that peripheral; the generated configuration has one attach per
    use, in order) and the constants the profile ties their pins to."""
    if not prof:
        return []
    ties = prof.get("tie") or {}
    occ, out = {}, []
    for k, att in enumerate(cfg.get("attach") or []):
        pid = att["peripheral"]
        i = occ.get(pid, 0)
        occ[pid] = i + 1
        if any(e.get("drop") and e.get("peripheral") == pid and int(e.get("index", 0)) == i
               for e in prof.get("attach") or []):
            binds = [str(v) for v in (att.get("bind") or {}).values()]
            mine = {key: val for key, val in ties.items()
                    if any(key == b or key.startswith(b + "[") for b in binds)}
            out.append({"use": k, "peripheral": pid, "ties": mine})
    return out


# ---------------------------------------------------------------- Verilog
# What a double click in the editor opens: the Verilog that defines the thing,
# from this checkout. The rig's top.sv is generated for the setup as it is on
# the page (saved or not); modules come from rtl/ (and the chosen design).

RTL_DIR = os.path.join(REPO, "rtl")
_MODULE_RE = re.compile(r"^\s*module\s+([A-Za-z_]\w*)", re.M)
IDENT_RE = re.compile(r"^[A-Za-z_]\w{0,127}$")
_SECTION_RE = re.compile(r"^    // ---- .* \(peripheral '[^']*'\) ----$")


def module_index():
    """{module name: (repo-relative path, 1-based line)} for every module under
    rtl/ (a simulation model only where nothing else defines the name)."""
    out = {}
    for root, _dirs, files in os.walk(RTL_DIR):
        for name in sorted(files):
            if not name.endswith((".sv", ".v", ".svh")):
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
            rel = os.path.relpath(path, REPO)
            for m in _MODULE_RE.finditer(text):
                sim = rel.startswith(os.path.join("rtl", "sim") + os.sep)
                if m.group(1) not in out or (not sim and out[m.group(1)][2]):
                    out[m.group(1)] = (rel, text.count("\n", 0, m.start(1)) + 1, sim)
    return {k: (p, line) for k, (p, line, _sim) in out.items()}


def module_source(name, design=None):
    """{path, text, line} of the module `name`: a design's own module when
    `design` names one that defines it, else rtl/."""
    if not IDENT_RE.match(str(name)):
        raise ApiError(400, "not a module name")
    if design is not None:
        if design not in list_designs():
            raise ApiError(400, "unknown design '{}'".format(design))
        ddir = os.path.join(DESIGNS_DIR, design)
        for fname in sorted(os.listdir(ddir)):
            if fname.endswith((".sv", ".v")):
                path = os.path.join(ddir, fname)
                with open(path, encoding="utf-8", errors="replace") as f:
                    text = f.read()
                m = re.search(r"^\s*module\s+" + name + r"\b", text, re.M)
                if m:
                    return {"path": os.path.relpath(path, REPO), "text": text,
                            "line": text.count("\n", 0, m.end()) + 1}
    idx = module_index()
    if name not in idx:
        raise ApiError(404, "no module '{}' under rtl/".format(name))
    rel, line = idx[name]
    with open(os.path.join(REPO, rel), encoding="utf-8", errors="replace") as f:
        return {"path": rel, "text": f.read(), "line": line}


def save_design(design, text, loaded):
    """Write the design's design_top file (the page's pinned, editable Source
    tab). `loaded` is the text the page started editing from: when the file
    no longer holds it (edited elsewhere since), nothing is written."""
    if not isinstance(text, str) or not isinstance(loaded, str):
        raise ApiError(400, "text and loaded must be strings")
    src = module_source("design_top", design)          # validates the design id
    path = os.path.realpath(os.path.join(REPO, src["path"]))
    if not path.startswith(os.path.realpath(os.path.join(DESIGNS_DIR, design)) + os.sep):
        raise ApiError(400, "the design's file is not under designs/{}".format(design))
    if src["text"] != loaded:
        raise ApiError(409, "{} changed on disk since the page loaded it: reload it (your edits stay in the "
                            "page until you do) or copy them out first".format(src["path"]))
    if text != loaded:
        tmp = path + ".saving"
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    return {"path": src["path"], "text": text}


def _ref_names(ref):
    """How a pinmap entry appears in top.sv: as written (`arduino_io[27]`),
    and its port name (`onboard_uart.tx` -> `onboard_uart_tx`, the bus of an
    indexed entry)."""
    ref = str(ref)
    base = re.sub(r"\[\d+\]$", "", ref).replace(".", "_")
    return ref.replace(".", "_"), base


def verilog_view(setup, target):
    """The rig's generated top.sv with the lines that define `target`:
      use          its part's section (its pins' lines when the profile left it out)
      refs         lines using these pinmap entries (within the use's section when given)
      design_port  the design_top instance's port line, and the lines of the
                   use's section that drive its capability bus
      parameter    the design_top instance's parameter line
      board        the top's port list
    plus the files of the modules it names (`module`, and any the section
    instantiates)."""
    target = target or {}
    kept, _excluded = _traceable(setup)
    cfg = su.generate(dict(setup, use=[setup["use"][k] for k in kept]))
    resolved = config_init.resolve_configuration(setup["id"], configuration=cfg)
    try:
        text = codegen.emit_top_sv(resolved)
        note = ""
    except codegen.CodegenError as exc:
        text = codegen.emit_top_sv(resolved, strict=False)
        note = "the build would refuse this rig: " + str(exc).splitlines()[0]
    lines = text.split("\n")
    heads = [k for k, l in enumerate(lines) if _SECTION_RE.match(l)]
    sections = {}
    if len(heads) == len(resolved["peripherals"]):
        for n, (k, a) in enumerate(zip(heads, resolved["peripherals"])):
            end = heads[n + 1] if n + 1 < len(heads) else next(
                (j for j in range(k + 1, len(lines)) if lines[j].startswith("    // ---- ")), len(lines))
            if a.get("attach_index") is not None:
                sections[kept[a["attach_index"]]] = (k, end)
    header_end = next((k for k, l in enumerate(lines) if l.strip() == ");"), 0)
    top_start = next((k for k, l in enumerate(lines) if l.startswith("module top")), 0)
    lab_start = next((k for k, l in enumerate(lines) if "i_design_top (" in l), len(lines))
    lab_params = next((k for k, l in enumerate(lines) if l.strip().startswith("design_top #")), lab_start)
    hi = set()

    use = target.get("use")
    span = sections.get(use) if use is not None else None
    refs = [r for r in target.get("refs") or [] if r]
    if span and not refs and not target.get("design_port"):
        hi.update(range(span[0], span[1]))
    unused = []
    for ref in refs:
        exact, base = _ref_names(ref)
        lo, hi_end = span if span else (header_end + 1, len(lines))
        found = [k for k in range(lo, hi_end) if exact in lines[k]] or \
                [k for k in range(lo, hi_end) if re.search(r"\b" + re.escape(base) + r"\b", lines[k])]
        found += [k for k in range(top_start, header_end) if re.search(r"\b" + re.escape(base) + r"\b", lines[k])]
        hi.update(found)
        if not found:
            unused.append(ref)
    if unused:
        note = "; ".join(filter(None, [note, "not used by this rig, so the generated top has no port for it: " +
                                       ", ".join(unused) + " (nothing in the rig is wired there)"]))
    port = target.get("design_port")
    if port:
        entry = next((e for e in codegen.design_ports() if e[0] == port), None)
        hi.update(k for k in range(lab_start, len(lines)) if re.match(r"^\s*\." + re.escape(port) + r"\(", lines[k]))
        if entry and span:
            bus = "cap_{}_{}".format(entry[1], entry[2])
            hi.update(k for k in range(*span) if re.search(r"\b" + re.escape(bus) + r"(\b|__)", lines[k]))
    param = target.get("parameter")
    if param:
        hi.update(k for k in range(lab_params, lab_start + 1) if re.match(r"^\s*\." + re.escape(param) + r"\(", lines[k]))
    if target.get("board"):
        hi.update(range(top_start, header_end + 1))

    files = [{"path": None, "title": "top.sv generated for " + setup["id"] + " (as on the page)", "text": text,
              "highlight": sorted(k + 1 for k in hi), "note": note}]
    names = []
    if target.get("module"):
        names.append(target["module"])
    if span:
        for k in range(*span):
            m = re.match(r"^\s*([A-Za-z_]\w*)\s*(#|\w+\s*\()", lines[k])
            if m and m.group(1) not in ("assign", "wire", "logic", "reg") and m.group(1) not in names:
                names.append(m.group(1))
    idx = module_index()
    design = target.get("design")
    for name in names:
        try:
            src = module_source(name, design if name == "design_top" and design else None)
        except ApiError:
            continue
        mlines = src["text"].split("\n")
        marks = []
        # the port or parameter the target names, as the module declares it
        for word, pattern in ((port, r"^\s*(input|output|inout)\b[^;]*\b{}\b"),
                              (param, r"^\s*(parameter\b[^;]*?\b)?{}\s*=")):
            if word:
                marks += [k + 1 for k, l in enumerate(mlines) if re.search(pattern.format(re.escape(word)), l)][:1]
        files.append({"path": src["path"], "title": "module " + name + (" of " + design if src["path"].startswith("designs") else ""),
                      "text": src["text"], "highlight": sorted(set(marks)) or [src["line"]], "note": ""})
    return {"files": files, "modules": sorted(idx)}


def _check_setup(setup):
    if not isinstance(setup, dict) or not ID_RE.match(str(setup.get("id", ""))):
        raise ApiError(400, "a setup id is 1-80 characters of a-z, 0-9 and _")
    if setup.get("board") not in su.read_layouts():
        raise ApiError(400, "unknown board '{}'".format(setup.get("board")))
    # an existing configuration without a setup belongs to the hand-written /
    # synced data: do not overwrite it from here
    if setup["id"] in config_init.read_configurations() and setup["id"] not in su.read_setups():
        raise ApiError(409, "configuration '{}' exists and has no setup; choose another id".format(setup["id"]))


def save(setup):
    _check_setup(setup)
    result = evaluate(setup)
    errors = [p["message"] for p in result["problems"] if p["level"] == "error"]
    if errors:
        raise ApiError(400, "not saved: " + "; ".join(errors))
    setup_path = su.write_setup(setup)
    cfg_path = su.configuration_path(setup["id"])
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write(result["configuration_text"])
    config_init.clear_cache()
    return {"setup": os.path.relpath(setup_path, REPO), "configuration": os.path.relpath(cfg_path, REPO)}


def project(setup_id, design):
    """The SystemVerilog project for a saved setup and a design:
    designs/<design>/run/<setup_id>/ (top.sv, constraints, toolchain project)."""
    if not ID_RE.match(str(setup_id)) or setup_id not in su.read_setups():
        raise ApiError(400, "save the setup first")
    if design not in list_designs():
        raise ApiError(400, "unknown design '{}'".format(design))
    from tools import cli, design_requirements as dr
    unmet = dr.check(config_init.resolve_configuration(setup_id), design_requirements(design),
                     config_init.read_capabilities())
    if unmet:
        raise ApiError(400, "{} does not fit {}: {}".format(design, setup_id, "; ".join(unmet)))
    design_dir = os.path.join(DESIGNS_DIR, design)
    rc = cli.prepare_design(design_dir, setup_id)
    out = cli.run_dir(design_dir, setup_id)
    files = sorted(os.path.relpath(os.path.join(root, f), out)
                   for root, _dirs, names in os.walk(out) for f in names)
    return {"ok": rc == 0, "output": os.path.relpath(out, REPO), "files": files}


def project_zip(setup_id, design):
    from tools import cli
    if not ID_RE.match(str(setup_id)) or design not in list_designs():
        raise ApiError(404, "no such project")
    out = cli.run_dir(os.path.join(DESIGNS_DIR, design), setup_id)
    if not os.path.isdir(out):
        raise ApiError(404, "generate the project first")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(os.path.join(DESIGNS_DIR, design, "design_top.sv"), "design_top.sv")
        for root, _dirs, names in os.walk(out):
            for f in names:
                full = os.path.join(root, f)
                z.write(full, os.path.join(setup_id, os.path.relpath(full, out)))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

_STATIC_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8"}


def _internal_error(exc):
    """The message for an unexpected failure (also printed with its traceback
    on the server's console); when the code changed since the server
    started, that is the likely cause."""
    import traceback
    traceback.print_exc()
    msg = "internal error: {}: {}".format(type(exc).__name__, exc)
    if _code_fingerprint() != _STARTED_WITH:
        msg += " — unifpga's code changed since this server started: restart ./unifpga serve"
    return msg


def make_server(port=8765, host="127.0.0.1"):
    import http.server

    class Handler(http.server.BaseHTTPRequestHandler):
        def _send(self, status, body, ctype="application/json", cache=True):
            data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            if not cache or ctype == "application/json":
                # the page and its answers always come from this server's code
                self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = self.path.split("?")[0]
            parts = [p for p in path.split("/") if p]
            try:
                if not parts or parts == ["index.html"]:
                    return self._static("index.html")
                if parts[0] in ("studio.js", "studio.css") and len(parts) == 1:
                    return self._static(parts[0])
                if parts[:2] == ["api", "designs"] and len(parts) == 2:
                    return self._send(200, design_table())
                if parts[:2] == ["api", "boards"]:
                    return self._send(200, sorted(su.read_layouts()))
                if parts[:2] == ["api", "board"] and len(parts) == 3:
                    return self._send(200, board_data(parts[2]))
                if parts[:2] == ["api", "setup"] and len(parts) == 3:
                    setups = su.read_setups()
                    if parts[2] not in setups:
                        raise ApiError(404, "no setup '{}'".format(parts[2]))
                    return self._send(200, setups[parts[2]])
                if parts[:2] == ["api", "project"] and len(parts) == 4 and parts[3].endswith(".zip"):
                    data = project_zip(parts[2], parts[3][:-4])
                    return self._send(200, data, "application/zip")
                raise ApiError(404, "not found")
            except ApiError as exc:
                return self._send(exc.status, {"error": str(exc)})
            except (su.SetupError, config_init.ConfigError, codegen.CodegenError) as exc:
                return self._send(400, {"error": str(exc)})
            except Exception as exc:                   # never drop the connection without an answer
                return self._send(500, {"error": _internal_error(exc)})

        def do_POST(self):
            try:
                self._check_origin()
                length = int(self.headers.get("Content-Length") or 0)
                if length > 1 << 20:
                    raise ApiError(413, "request too large")
                body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                path = self.path.split("?")[0]
                if path == "/api/evaluate":
                    return self._send(200, evaluate(body["setup"]))
                if path == "/api/autowire":
                    return self._send(200, su.autowire(body["setup"], int(body["use"])))
                if path == "/api/save":
                    return self._send(200, save(body["setup"]))
                if path == "/api/verilog":
                    return self._send(200, verilog_view(body["setup"], body.get("target")))
                if path == "/api/design/save":
                    return self._send(200, save_design(body["design"], body["text"], body["loaded"]))
                if path == "/api/module":
                    return self._send(200, module_source(body["name"], body.get("design")))
                if path == "/api/project":
                    return self._send(200, project(body.get("setup_id"), body.get("design")))
                raise ApiError(404, "not found")
            except ApiError as exc:
                return self._send(exc.status, {"error": str(exc)})
            except (KeyError, ValueError, TypeError) as exc:
                return self._send(400, {"error": "bad request: {}".format(exc)})
            except (su.SetupError, config_init.ConfigError, codegen.CodegenError) as exc:
                return self._send(400, {"error": str(exc)})
            except Exception as exc:
                return self._send(500, {"error": _internal_error(exc)})

        def _check_origin(self):
            # a cross-site page can POST to localhost, but not with a custom
            # header (the preflight fails) and not with our Origin
            if self.headers.get(WRITE_HEADER) != "1":
                raise ApiError(403, "missing " + WRITE_HEADER)
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
            if host not in ("127.0.0.1", "localhost"):
                raise ApiError(403, "foreign host")        # DNS rebinding
            origin = self.headers.get("Origin")
            if origin and not re.match(r"^http://(127\.0\.0\.1|localhost)(:\d+)?$", origin):
                raise ApiError(403, "foreign origin")

        def _static(self, name):
            with open(os.path.join(STATIC, name), "rb") as f:
                return self._send(200, f.read(), _STATIC_TYPES[os.path.splitext(name)[1]], cache=False)

        def log_message(self, fmt, *args):
            pass

    return http.server.ThreadingHTTPServer((host, port), Handler)


def serve(port=8765, host="127.0.0.1"):
    httpd = make_server(port, host)
    print("Board editor on http://{}:{}/ (Ctrl-C stops)".format(host, port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def standalone_page(setup_id=None, board_id=None):
    """The editor page with its data inlined, read-only (./unifpga view)."""
    if setup_id:
        setups = su.read_setups()
        if setup_id not in setups:
            raise su.SetupError("no setup '{}'".format(setup_id))
        setup = setups[setup_id]
        board_id = setup["board"]
    else:
        setup = {"id": board_id, "board": board_id, "toolchain": "", "use": []}
    data = {"board": board_data(board_id), "setup": setup,
            "evaluation": evaluate(setup) if setup_id else {"problems": [], "trace": None}}
    with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as f:
        page = f.read()
    with open(os.path.join(STATIC, "studio.css"), encoding="utf-8") as f:
        css = f.read()
    with open(os.path.join(STATIC, "studio.js"), encoding="utf-8") as f:
        js = f.read()
    inline = json.dumps(data).replace("</", "<\\/")
    page = page.replace('<link rel="stylesheet" href="studio.css">', "<style>" + css + "</style>")
    page = page.replace('<script src="studio.js"></script>',
                        "<script>window.STUDIO_STATIC = " + inline + ";</script><script>" + js.replace("</script", "<\\/script") + "</script>")
    return page

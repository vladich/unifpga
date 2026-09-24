"""
Setups: a configuration described as a physical rig.

A setup (config/setups/<id>.yml) names a board, a toolchain and, in attach
order, what is used on it:

    - onboard: <id>              an on-board device of the board's layout
      params: {...}              (merged over the layout's attach params)
    - module: <id>               an add-on module (config/modules/<id>.yml)
      wires: {<module pin>: <connector>.<pin>, ...}
      plug: {connector: jd, row: 2}   (instead of wires: a Pmod module plugged in)
      params: {...}
    - gpio: <connector>          the connector's pins as the design's gpio bus
      params: {...}
    - raw: {<attach>}            an attach the physical model does not cover

The board layout (config/layouts/<board>.yml) says which pinmap entry every
connector pin is and what the on-board devices attach as; connector types
(config/connectors.yml) give pin numbering, power pins and voltage.

generate() turns a setup into the configuration dict config/configurations/
holds; derive() goes the other way for an existing configuration, and
check_roundtrip() proves the two agree. validate() reports rig problems: pins
used twice, unknown connectors or pins, module signals the peripheral does not
have, required signals left unwired, voltage mismatches.
"""

import copy
import os
import re

from config import init as config_init
from tools import codegen

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")
SETUP_DIR = os.path.join(CONFIG_DIR, "setups")

_INDEXED = re.compile(r"^(\w+)\[(\d+)\]$")
_PASSIVE = ("power", "ground")


class SetupError(Exception):
    pass


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------

def read_connectors():
    return config_init._load_yaml(os.path.join(CONFIG_DIR, "connectors.yml"), "Connectors")


def read_layouts():
    return config_init._load_yaml_dir("layouts", "Layout", "board")


def read_layout(board_id):
    layout = read_layouts().get(board_id)
    if layout is None:
        raise SetupError("no layout for board '{}' (config/layouts/{}.yml)".format(board_id, board_id))
    return layout


def read_modules():
    return config_init._load_yaml_dir("modules", "Module", "id")


def read_setups():
    """{id: setup} from SETUP_DIR (the same directory write_setup() writes)."""
    out = {}
    if not os.path.isdir(SETUP_DIR):
        return out
    for name in sorted(os.listdir(SETUP_DIR)):
        if name.endswith(".yml") and not name.startswith("_"):
            data = config_init._read_yaml_file(os.path.join(SETUP_DIR, name)) or {}
            setup = data.get("Setup")
            if setup and setup.get("id"):
                out[setup["id"]] = setup
    return out


def read_setup(setup_id):
    setup = read_setups().get(setup_id)
    if setup is None:
        raise SetupError("no setup '{}' (config/setups/{}.yml)".format(setup_id, setup_id))
    return setup


def connector(layout, conn_id):
    for c in layout.get("connectors") or []:
        if c["id"] == conn_id:
            return c
    raise SetupError("board '{}' has no connector '{}'".format(layout["board"], conn_id))


def onboard_item(layout, item_id):
    for o in layout.get("onboard") or []:
        if o["id"] == item_id:
            return o
    raise SetupError("board '{}' has no on-board item '{}'".format(layout["board"], item_id))


def pin_ref(layout, where):
    """`jd.7` -> the pinmap reference that connector pin is (`pmod_jd[4]`)."""
    conn_id, _, pin = str(where).partition(".")
    c = connector(layout, conn_id)
    pins = c.get("pins") or {}
    for key, ref in pins.items():
        if str(key) == pin:
            return ref
    raise SetupError("connector '{}' has no signal pin '{}'".format(conn_id, pin))


def ref_index(layout):
    """{pinmap reference: `connector.pin`} over every connector of the layout."""
    out = {}
    for c in layout.get("connectors") or []:
        for key, ref in (c.get("pins") or {}).items():
            out[ref] = "{}.{}".format(c["id"], key)
    return out


def plug_wires(connectors, layout, module, plug):
    """{module pin: connector.pin} for a module plugged whole into a connector
    (`form: pmod_1x6` into one row of a 2x6 Pmod)."""
    c = connector(layout, plug["connector"])
    ctype = connectors.get(c["type"]) or {}
    rows = ctype.get("rows") or []
    if module.get("form") != "pmod_1x6" or not rows:
        raise SetupError("module '{}' cannot be plugged into '{}'".format(module["id"], c["id"]))
    row = rows[int(plug.get("row", 1)) - 1]
    return {str(k): "{}.{}".format(c["id"], row[int(k) - 1])
            for k, sig in module["pins"].items() if sig not in _PASSIVE}


# ---------------------------------------------------------------------------
# setup -> configuration
# ---------------------------------------------------------------------------

def _module_attach(connectors, layout, modules, use):
    module = modules.get(use["module"])
    if module is None:
        raise SetupError("unknown module '{}'".format(use["module"]))
    wires = use.get("wires")
    if "plug" in use:
        wires = plug_wires(connectors, layout, module, use["plug"])
    order, scalars, vectors = [], {}, {}      # bind keeps the wires' order (codegen emits in it)
    for pin, where in (wires or {}).items():
        sig = module["pins"].get(str(pin))
        if sig is None:
            raise SetupError("module '{}' has no pin '{}'".format(module["id"], pin))
        if sig in _PASSIVE:
            continue
        ref = pin_ref(layout, where)
        m = _INDEXED.match(sig)
        name = m.group(1) if m else sig
        if name not in order:
            order.append(name)
        if m:
            vectors.setdefault(name, {})[int(m.group(2))] = ref
        else:
            scalars[name] = ref
    bind = {}
    for name in order:
        if name in scalars:
            bind[name] = scalars[name]
            continue
        bits = vectors[name]
        if sorted(bits) != list(range(len(bits))):
            raise SetupError("module '{}': {} is not wired bit 0 upwards".format(module["id"], name))
        bind[name] = [bits[i] for i in range(len(bits))]
    attach = {"peripheral": module["peripheral"]}
    if use.get("params"):
        attach["params"] = copy.deepcopy(use["params"])
    attach["bind"] = bind
    return attach


def generate(setup):
    """The configuration dict (the `Configuration:` mapping) for a setup."""
    layout = read_layout(setup["board"])
    modules = read_modules()
    connectors = read_connectors()
    cfg = {"id": setup["id"], "board": setup["board"], "toolchain": setup["toolchain"]}
    if setup.get("part") is not None:
        cfg["part"] = setup["part"]
    attach = []
    for use in setup.get("use") or []:
        if "onboard" in use:
            t = onboard_item(layout, use["onboard"])["attach"]
            params = dict(t.get("params") or {}, **(use.get("params") or {}))
            a = {"peripheral": t["peripheral"]}
            if params:
                a["params"] = params
            a["bind"] = copy.deepcopy(t["bind"])
            attach.append(copy.deepcopy(a))
        elif "module" in use:
            attach.append(_module_attach(connectors, layout, modules, use))
        elif "gpio" in use:
            c = connector(layout, use["gpio"])
            if not c.get("bank"):
                raise SetupError("connector '{}' is not one pinmap bank: it cannot be the design's gpio".format(c["id"]))
            a = {"peripheral": "gpio_header"}
            if use.get("params"):
                a["params"] = copy.deepcopy(use["params"])
            a["bind"] = {"io": c["bank"]}
            attach.append(a)
        elif "raw" in use:
            attach.append(copy.deepcopy(use["raw"]))
        else:
            raise SetupError("setup '{}': a use needs onboard, module, gpio or raw: {}".format(setup["id"], use))
    cfg["attach"] = attach
    for k, v in (setup.get("extra") or {}).items():
        cfg[k] = copy.deepcopy(v)
    return cfg


# ---------------------------------------------------------------------------
# configuration -> setup
# ---------------------------------------------------------------------------

def _params(a):
    return a.get("params") or {}


def _derive_module(a, layout, modules, connectors, refs):
    """A module use for attach `a` when some module of its peripheral covers
    every bound signal and every reference is a connector pin; else None."""
    for module in modules.values():
        if module["peripheral"] != a["peripheral"]:
            continue
        by_signal = {sig: pin for pin, sig in module["pins"].items() if sig not in _PASSIVE}
        wires = {}
        for sig, ref in (a.get("bind") or {}).items():
            items = [(sig, ref)] if not isinstance(ref, list) else \
                    [("{}[{}]".format(sig, i), r) for i, r in enumerate(ref)]
            for s, r in items:
                if s not in by_signal or r not in refs:
                    wires = None
                    break
                wires[by_signal[s]] = refs[r]
            if wires is None:
                break
        if not wires:
            continue
        use = {"module": module["id"]}
        plug = _as_plug(connectors, layout, module, wires)
        if plug:
            use["plug"] = plug
        else:
            use["wires"] = wires
        if _params(a):
            use["params"] = copy.deepcopy(a["params"])
        return use
    return None


def _as_plug(connectors, layout, module, wires):
    if module.get("form") != "pmod_1x6":
        return None
    conns = {w.partition(".")[0] for w in wires.values()}
    if len(conns) != 1:
        return None
    conn_id = conns.pop()
    rows = (connectors.get(connector(layout, conn_id)["type"]) or {}).get("rows") or []
    for r in range(len(rows)):
        plug = {"connector": conn_id, "row": r + 1}
        if list(plug_wires(connectors, layout, module, plug).items()) == list(wires.items()):
            return plug
    return None


def derive(configuration):
    """The setup that generates `configuration` (a `Configuration:` dict).
    Attaches the physical model does not cover are kept as `raw` uses."""
    layout = read_layout(configuration["board"])
    modules = read_modules()
    connectors = read_connectors()
    refs = ref_index(layout)
    banks = {c.get("bank"): c["id"] for c in layout.get("connectors") or [] if c.get("bank")}
    setup = {"id": configuration["id"], "board": configuration["board"], "toolchain": configuration["toolchain"]}
    if configuration.get("part") is not None:
        setup["part"] = configuration["part"]
    uses = []
    for a in configuration.get("attach") or []:
        use = None
        for o in layout.get("onboard") or []:
            t = o["attach"]
            if t["peripheral"] == a["peripheral"] and ordered(t.get("bind")) == ordered(a.get("bind")) \
                    and list(a) == [k for k in ("peripheral", "params", "bind") if k in a]:
                use = {"onboard": o["id"]}
                base, have = _params(t), _params(a)
                if have != base:
                    if any(k not in have for k in base):
                        use = None           # a template parameter dropped: not an override
                        continue
                    use["params"] = {k: v for k, v in have.items() if base.get(k) != v}
                if list(dict(base, **use.get("params", {}))) != list(have):
                    use = None                   # the configuration orders its params otherwise
                    continue
                break
        if use is None and a["peripheral"] == "gpio_header" and (a.get("bind") or {}).get("io") in banks \
                and set(a["bind"]) == {"io"}:
            use = {"gpio": banks[a["bind"]["io"]]}
            if _params(a):
                use["params"] = copy.deepcopy(a["params"])
        if use is None:
            use = _derive_module(a, layout, modules, connectors, refs)
        if use is None:
            use = {"raw": copy.deepcopy(a)}
        uses.append(use)
    notes = file_notes(configuration["id"])
    if notes:
        setup["notes"] = notes
    setup["use"] = uses
    extra = {k: copy.deepcopy(v) for k, v in configuration.items()
             if k not in ("id", "board", "toolchain", "part", "attach")}
    if extra:
        setup["extra"] = extra
    return setup


_STANDARD_NOTE = re.compile(r"^(Configuration '.*'\.|Generated from config/setups/.*)$")


def file_notes(config_id):
    """The comment lines heading config/configurations/<id>.yml other than the
    ones emit_configuration() writes itself."""
    notes = []
    path = configuration_path(config_id)
    if not os.path.exists(path):
        return notes
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.startswith("#"):
                break
            text = line[1:].strip()
            if text and not _STANDARD_NOTE.match(text):
                notes.append(text)
    return notes


def ordered(value):
    """`value` with every mapping as a list of (key, value) pairs, so that two
    values compare equal only with their keys in the same order (codegen emits
    ports and constraints in bind / params order)."""
    if isinstance(value, dict):
        return [(k, ordered(v)) for k, v in value.items()]
    if isinstance(value, list):
        return [ordered(v) for v in value]
    return value


def same_configuration(a, b):
    """Equal configurations: the same top-level keys, and each value equal with
    its nested keys in the same order (the top-level order does not matter)."""
    return set(a) == set(b) and all(ordered(a[k]) == ordered(b[k]) for k in a)


def check_roundtrip(configuration):
    """[] when derive() then generate() gives `configuration` back, keys in
    the same order; else the differences, one line each."""
    got = generate(derive(configuration))
    diffs = []
    for k in sorted(set(got) | set(configuration)):
        if k == "attach":
            continue
        if ordered(got.get(k)) != ordered(configuration.get(k)):
            diffs.append("{}: {!r} != {!r}".format(k, got.get(k), configuration.get(k)))
    ga, ca = got.get("attach") or [], configuration.get("attach") or []
    if len(ga) != len(ca):
        diffs.append("attach count {} != {}".format(len(ga), len(ca)))
    for i, (g, c) in enumerate(zip(ga, ca)):
        if ordered(g) != ordered(c):
            diffs.append("attach {} ({}): {!r} != {!r}".format(i, c.get("peripheral"), g, c))
    return diffs


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def use_label(use, attach):
    """How a use is named in messages and drawings."""
    if "onboard" in use:
        return use["onboard"]
    if "module" in use:
        return use["module"]
    if "gpio" in use:
        return "gpio {}".format(use["gpio"])
    return "raw {}".format(attach.get("peripheral"))


def _voltage_range(v):
    """`3.3` or `[3.3, 5.0]` -> (low, high); (None, None) when not stated."""
    if v is None:
        return None, None
    if isinstance(v, (list, tuple)):
        return min(v), max(v)
    return v, v


def validate(setup):
    """[(level, message)]: level 'error' or 'warning'."""
    problems = []
    try:
        layout = read_layout(setup["board"])
        cfg = generate(setup)
    except SetupError as exc:
        return [("error", str(exc))]
    pinmap = config_init.read_board_pinmap(setup["board"]) or {}
    peripherals = config_init.read_peripherals()
    modules = read_modules()
    connectors = read_connectors()
    toolchains = set(config_init.read_toolchains())
    if setup["toolchain"] not in toolchains:
        problems.append(("error", "unknown toolchain '{}'".format(setup["toolchain"])))

    owner = {}
    covered = plugged_row_refs(setup, layout, connectors)
    for ref, k in covered.items():
        for _bit, pin in codegen._bind_pins(pinmap, ref):
            for p in str(pin or "").split(","):
                if p:
                    owner.setdefault(p, (k, use_label(setup["use"][k], {}), False))
    for n, (use, a) in enumerate(zip(setup.get("use") or [], cfg["attach"])):
        label = use_label(use, a)
        contract = peripherals.get(a.get("peripheral"))
        if contract is None:
            problems.append(("error", "{}: unknown peripheral '{}'".format(label, a.get("peripheral"))))
            continue
        names = {s["name"]: s for s in contract.get("signals") or []}
        for sig in a.get("bind") or {}:
            if sig not in names:
                problems.append(("error", "{}: peripheral {} has no signal '{}'".format(label, a["peripheral"], sig)))
        if "module" in use:
            for name, s in names.items():
                if not s.get("optional") and name not in (a.get("bind") or {}) and \
                        name not in (a.get("params") or {}):
                    problems.append(("error", "{}: required signal '{}' is not wired".format(label, name)))
            module = modules.get(use["module"]) or {}
            conns = {w.partition(".")[0] for w in (use.get("wires") or {}).values()}
            if "plug" in use:
                conns = {use["plug"]["connector"]}
            lo, hi = _voltage_range(module.get("voltage"))
            for conn_id in conns:
                v = (connectors.get(connector(layout, conn_id)["type"]) or {}).get("voltage")
                if v and lo is not None and not lo <= v <= hi:
                    problems.append(("error", "{}: a module for {} V on the {} V connector {}".format(
                        label, lo if lo == hi else "{}-{}".format(lo, hi), v, conn_id)))
        for sig, ref in (a.get("bind") or {}).items():
            for port_bit, pin in codegen._bind_pins(pinmap, ref):
                if pin is None:
                    problems.append(("error", "{}: {} ({}) is not a pin of the board".format(label, sig, port_bit)))
                    continue
                for p in str(pin).split(","):
                    prev = owner.get(p)          # (use index, label, is gpio)
                    if prev and prev[0] != n and not (prev[2] or "gpio" in use):
                        problems.append(("error", "pin {} used by both {} and {}".format(p, prev[1], label)))
                    owner.setdefault(p, (n, label, "gpio" in use))
    return problems


# ---------------------------------------------------------------------------
# files
# ---------------------------------------------------------------------------

def _flow(value):
    import yaml
    return yaml.safe_dump(value, default_flow_style=True, sort_keys=False, width=100000).strip()


def dump_setup(setup):
    """The text of config/setups/<id>.yml: one line per use, wires and
    parameters in flow style."""
    L = ["# Setup {}: the rig config/configurations/{}.yml describes.".format(setup["id"], setup["id"]),
         "# tools/setup.py generates that configuration from it (./unifpga setup check).",
         "", "Setup:"]
    for k in ("id", "board", "toolchain", "part"):
        if setup.get(k) is not None:
            L.append("  {}: {}".format(k, _scalar(setup[k])))
    if setup.get("notes"):
        L.append("  notes:")
        L.extend("    - {}".format(_scalar(n)) for n in setup["notes"])
    L.append("  use:")
    for use in setup.get("use") or []:
        head = next(k for k in ("onboard", "module", "gpio", "raw") if k in use)
        if head == "raw":
            L.append("    - raw: {}".format(_flow(use["raw"])))
            continue
        L.append("    - {}: {}".format(head, use[head]))
        for k in ("plug", "wires", "params"):
            if k in use:
                L.append("      {}: {}".format(k, _flow(use[k])))
    if setup.get("extra"):
        import yaml
        L.append("  extra:")
        text = yaml.safe_dump(setup["extra"], sort_keys=False, width=100)
        L.extend("    " + line for line in text.rstrip("\n").split("\n"))
    return "\n".join(L) + "\n"


def write_setup(setup):
    os.makedirs(SETUP_DIR, exist_ok=True)
    path = os.path.join(SETUP_DIR, setup["id"] + ".yml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(dump_setup(setup))
    return path


# ---------------------------------------------------------------------------
# configuration files
# ---------------------------------------------------------------------------

_PLAIN = re.compile(r"^[A-Za-z_][\w.\-/ ]*$")
_TOP_COMMENTS = {
    "io_overrides": "Gowin IO_TYPE this configuration states beyond the board-wide ones",
    "tie": "Pins held at a constant or driven from the reset — `assign PIN = 1'b0` / `~ rst`",
}


def _scalar(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v)
    if _PLAIN.match(s) and s not in ("true", "false", "null", "yes", "no", "on", "off") and not s.endswith(" "):
        return s
    import json
    return json.dumps(s, ensure_ascii=False)


def _inline(v):
    if isinstance(v, list):
        return "[" + ", ".join(_inline(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join("{}: {}".format(_scalar(k), _inline(x)) for k, x in v.items()) + "}"
    return _scalar(v)


def _block(lines, indent, mapping):
    for k, v in mapping.items():
        if isinstance(v, dict) and v and not any(isinstance(x, (dict, list)) for x in v.values()) and indent >= 8:
            lines.append("{}{}: {}".format(" " * indent, _scalar(k), _inline(v)))
        elif isinstance(v, dict):
            lines.append("{}{}:".format(" " * indent, _scalar(k)))
            _block(lines, indent + 2, v)
        else:
            lines.append("{}{}: {}".format(" " * indent, _scalar(k), _inline(v)))


def emit_configuration(cfg, notes=None):
    """The text of config/configurations/<id>.yml for a configuration dict."""
    L = ["# Configuration '{}'.".format(cfg["id"])]
    L += ["# " + n for n in (notes or [])]
    L += ["# Generated from config/setups/{}.yml (./unifpga setup generate); edit the setup.".format(cfg["id"]),
          "", "Configuration:"]
    for k in ("id", "board", "toolchain"):
        L.append("  {}: {}".format(k, _scalar(cfg[k])))
    if cfg.get("part") is not None:
        L.append("  part: {}   # which of the board's chips this configuration targets".format(_scalar(cfg["part"])))
    rest = [k for k in cfg if k not in ("id", "board", "toolchain", "part", "attach")]
    for k in [k for k in rest if k != "tie"]:
        L.append("")
        if k in _TOP_COMMENTS:
            L.append("  # " + _TOP_COMMENTS[k])
        _block(L, 2, {k: cfg[k]})
    L += ["", "  attach:"]
    for a in cfg.get("attach") or []:
        first = True
        for k, v in a.items():
            prefix = "    - " if first else "      "
            first = False
            if isinstance(v, dict):
                L.append("{}{}:".format(prefix, k))
                _block(L, 8, v)
            else:
                L.append("{}{}: {}".format(prefix, k, _inline(v)))
    if "tie" in cfg:
        L += ["", "  # " + _TOP_COMMENTS["tie"]]
        _block(L, 2, {"tie": cfg["tie"]})
    return "\n".join(L) + "\n"


def configuration_path(config_id):
    return os.path.join(CONFIG_DIR, "configurations", config_id + ".yml")


def generated_text(setup):
    return emit_configuration(generate(setup), setup.get("notes"))


# ---------------------------------------------------------------------------
# auto-wiring
# ---------------------------------------------------------------------------

def plugged_row_refs(setup, layout, connectors, skip=None):
    """{pinmap ref: use index} for every signal pin under a plugged module:
    the module covers its whole row, the pins it leaves unconnected too."""
    out = {}
    for k, use in enumerate(setup.get("use") or []):
        if k == skip or "plug" not in use:
            continue
        c = connector(layout, use["plug"]["connector"])
        rows = (connectors.get(c["type"]) or {}).get("rows") or []
        row = rows[int(use["plug"].get("row", 1)) - 1] if rows else []
        pins = {str(k2): ref for k2, ref in (c.get("pins") or {}).items()}
        for key in row:
            if str(key) in pins:
                out[pins[str(key)]] = k
    return out


def used_pins(setup, skip=None):
    """FPGA pins the setup's uses occupy (all but use `skip`), the pins under
    a plugged module included."""
    pinmap = config_init.read_board_pinmap(setup["board"]) or {}
    rest = dict(setup, use=[u for k, u in enumerate(setup.get("use") or []) if k != skip])
    used = set()
    refs = [ref for a in generate(rest)["attach"] for ref in (a.get("bind") or {}).values()]
    refs += list(plugged_row_refs(setup, read_layout(setup["board"]), read_connectors(), skip=skip))
    for ref in refs:
        for _bit, pin in codegen._bind_pins(pinmap, ref):
            if pin:
                used.update(str(pin).split(","))
    return used


def autowire(setup, index):
    """{"plug": ...} or {"wires": ...} for module use `index`: a Pmod module
    plugged into the first free Pmod row, any other module wired in order to
    the first connector with enough free pins at a voltage it runs at. Pins
    other uses occupy (on-board devices, gpio headers, modules) are avoided."""
    use = setup["use"][index]
    modules, ctypes = read_modules(), read_connectors()
    module = modules.get(use.get("module"))
    if module is None:
        raise SetupError("use {} is not a module".format(index))
    layout = read_layout(setup["board"])
    pinmap = config_init.read_board_pinmap(setup["board"]) or {}
    used = used_pins(setup, skip=index)
    lo, hi = _voltage_range(module.get("voltage"))
    need = [p for p, sig in module["pins"].items() if sig not in _PASSIVE]

    def free(conn, key):
        ref = (conn.get("pins") or {}).get(key, (conn.get("pins") or {}).get(int(key) if str(key).isdigit() else key))
        if ref is None:
            return False
        pins = [p for _b, p in codegen._bind_pins(pinmap, ref)]
        return pins and all(p and not set(str(p).split(",")) & used for p in pins)

    candidates = []
    for c in layout.get("connectors") or []:
        v = (ctypes.get(c["type"]) or {}).get("voltage")
        if lo is None or v is None or lo <= v <= hi:
            candidates.append(c)
    if module.get("form") == "pmod_1x6":
        for c in candidates:
            rows = (ctypes.get(c["type"]) or {}).get("rows") or []
            for r in range(len(rows)):
                plug = {"connector": c["id"], "row": r + 1}
                try:
                    wires = plug_wires(ctypes, layout, module, plug)
                except SetupError:
                    continue
                if all(free(c, w.partition(".")[2]) for w in wires.values()):
                    return {"plug": plug}
    spare = []                       # (connector, [free keys]) in layout order
    for c in candidates:
        rows = (ctypes.get(c["type"]) or {}).get("rows")
        keys = [str(k) for row in rows for k in row] if rows else [str(k) for k in c.get("pins") or {}]
        spare.append((c, [k for k in keys if free(c, k)]))
    for c, keys in spare:            # one connector when one has room
        if len(keys) >= len(need):
            return {"wires": {p: "{}.{}".format(c["id"], k) for p, k in zip(need, keys)}}
    # else across connectors, the roomiest first (a PmodVGA spans two Pmods)
    pool = ["{}.{}".format(c["id"], k) for c, keys in sorted(spare, key=lambda x: -len(x[1])) for k in keys]
    if len(pool) >= len(need):
        return {"wires": dict(zip(need, pool))}
    raise SetupError("{} needs {} free pins; the board has {} left at {} V".format(
        module.get("name") or module["id"], len(need), len(pool),
        lo if lo == hi else "{}-{}".format(lo, hi)))

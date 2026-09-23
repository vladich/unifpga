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
    return config_init._load_yaml_dir("setups", "Setup", "id") if os.path.isdir(SETUP_DIR) else {}


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
    bind, vectors = {}, {}
    for pin, where in (wires or {}).items():
        sig = module["pins"].get(str(pin))
        if sig is None:
            raise SetupError("module '{}' has no pin '{}'".format(module["id"], pin))
        if sig in _PASSIVE:
            continue
        ref = pin_ref(layout, where)
        m = _INDEXED.match(sig)
        if m:
            vectors.setdefault(m.group(1), {})[int(m.group(2))] = ref
        else:
            bind[sig] = ref
    for name, bits in vectors.items():
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
            a = copy.deepcopy(onboard_item(layout, use["onboard"])["attach"])
            if use.get("params"):
                a["params"] = dict(a.get("params") or {}, **use["params"])
            attach.append(a)
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
        if plug_wires(connectors, layout, module, plug) == wires:
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
            if t["peripheral"] == a["peripheral"] and t.get("bind") == a.get("bind"):
                use = {"onboard": o["id"]}
                base, have = _params(t), _params(a)
                if have != base:
                    if any(k not in have for k in base):
                        use = None           # a template parameter dropped: not an override
                        continue
                    use["params"] = {k: v for k, v in have.items() if base.get(k) != v}
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
    setup["use"] = uses
    extra = {k: copy.deepcopy(v) for k, v in configuration.items()
             if k not in ("id", "board", "toolchain", "part", "attach")}
    if extra:
        setup["extra"] = extra
    return setup


def check_roundtrip(configuration):
    """[] when derive() then generate() gives `configuration` back; else the
    differences, one line each."""
    got = generate(derive(configuration))
    diffs = []
    for k in sorted(set(got) | set(configuration)):
        if k == "attach":
            continue
        if got.get(k) != configuration.get(k):
            diffs.append("{}: {!r} != {!r}".format(k, got.get(k), configuration.get(k)))
    ga, ca = got.get("attach") or [], configuration.get("attach") or []
    if len(ga) != len(ca):
        diffs.append("attach count {} != {}".format(len(ga), len(ca)))
    for i, (g, c) in enumerate(zip(ga, ca)):
        if g != c:
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
            L.append("  {}: {}".format(k, setup[k]))
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

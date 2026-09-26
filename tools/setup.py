"""
Setups: a rig — one file, config/setups/<id>.yml.

A setup names a board, a toolchain and, in attach order, what is used on it:

    - onboard: <id>              an on-board device of the board's layout
      params: {...}              (merged over the layout's attach params)
      pins: [0, 1, 2, 4]         (some of the part's pins, in this order)
    - module: <id>               an add-on module (config/modules/<id>.yml)
      wires: {<module pin>: <connector>.<pin>, ...}
      plug: {connector: jd, row: 2}   (instead of wires: a module plugged by its
                                       numbered header; reversed: true = rotated)
      params: {...}
    - gpio: <connector>          the connector's pins as the design's gpio bus
      params: {...}
    - raw: {<attach>}            an attach the physical model does not cover

and how the design (design_top, the virtual device) sees it — on a use:

      bind: {dp: [onboard_leds[4], ...], hs: null}   a signal routed onto other
                                       pins, or (null) left unwired and tied off
                                       (a convention, like the design section)
      design_bits: {leds: [0, 1, 2, ~, 3]}   which bit of the design's bus each of
                                       the part's bits is (~: none); absent, the
                                       part takes the next free bits in attach order

and, for the rig, a `design:` section:

    design:
      reset: {sources: [{key: 0}], sync: 2}  which key / switch resets, and how
      clock: pixel | {name: lab, mhz: 50}    the clock design_top runs on
      uart_rx: 0 | 1                         what uart_rx reads with no UART pin
      width: {buttons: 8}                    a design bus wider than the bits wired to it
      tie: {<pin>: rst | ~rst | 0 | 1}       pins driven from the reset or tied off

`extra:` holds any other configuration key (io_overrides, pin_overrides, tie
for the hardware's sake, manual). The rig's configuration — the dict codegen
reads — is generate()'s expansion of the setup with the board's layout; it is
computed when a rig is loaded and never a file of the repository
(./unifpga setup show <id> prints it). UNIFPGA_PROFILE=0 (synthesize.py
--no-profile) leaves the design section and the design_bits out: buses
concatenated in attach order, a power-up reset.

The board as drawn (its headers and parts, in its file; drawn_boards) says which bank pin every
connector pin is and what the on-board devices attach as; connector types
(config/connectors.yml) give pin numbering, power pins and voltage.

generate() turns a setup into its configuration dict; derive() goes the other
way for a configuration dict from elsewhere, and check_roundtrip() proves the
two agree. validate() reports rig problems: pins used twice, unknown
connectors or pins, module signals the peripheral does not have, required
signals left unwired, voltage mismatches.
"""

import copy
import os
import re
from collections import OrderedDict

from config import init as config_init
from config import overlay
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

def gpio_passthrough():
    """(peripheral, signal) a setup's `gpio: <connector>` attaches: the
    peripheral the gpio capability names as its passthrough (gpio_header)
    and the capability's bidirectional signal (io)."""
    for c in config_init.read_capabilities().values():
        if c.get("passthrough"):
            sig = next(s["name"] for s in c.get("signals") or [] if s.get("direction") == "inout")
            return c["passthrough"], sig
    raise SetupError("no capability names a passthrough peripheral (config/capabilities/*.yml, passthrough:)")


def read_connectors():
    """Connector types: config/connectors.yml plus the ones boards carry for
    their own headers (`connector_types:`, from the board-sources registry)."""
    out = dict(config_init._load_yaml(os.path.join(CONFIG_DIR, "connectors.yml"), "Connectors") or {})
    for board_id, board in sorted(config_init._boards_index().items()):
        for tid, ctype in (board.get("connector_types") or {}).items():
            if tid in out and out[tid] != ctype:
                raise SetupError("connector type '{}' of board '{}' is defined differently elsewhere".format(tid, board_id))
            out[tid] = copy.deepcopy(ctype)
    return out


def drawn_boards():
    """{board id: board} for every board drawn — its headers and parts follow
    the `layout:` line of its file config/boards/<producer>/<family>/<id>.yml.
    The cached originals (config.init.peek_boards): read-only."""
    return {board_id: board for board_id, board in config_init.peek_boards().items() if "layout" in board}


def read_drawn(board_id):
    """One drawn board (the cached original, read-only); SetupError when the
    board is not drawn."""
    board = config_init.peek_board(board_id)
    if board is None or "layout" not in board:
        raise SetupError("board '{}' is not drawn yet (no headers and parts in its file; ./unifpga layout draft {})"
                         .format(board_id, board_id))
    return board


def read_modules():
    return config_init._load_yaml_dir("modules", "Module", "id")


def read_setups():
    """{id: setup} from SETUP_DIR (the same directory write_setup() writes)."""
    return config_init._load_yaml_dir("setups", "Setup", "id", base=SETUP_DIR,
                                      missing_ok=True)


def read_setup(setup_id):
    setup = read_setups().get(setup_id)
    if setup is None:
        raise SetupError("no setup '{}' (config/setups/{}.yml)".format(setup_id, setup_id))
    return setup


def connector(layout, conn_id):
    for c in layout.get("headers") or []:
        if c["id"] == conn_id:
            return c
    raise SetupError("board '{}' has no connector '{}'".format(layout["id"], conn_id))


def onboard_item(layout, item_id):
    for o in layout.get("parts") or []:
        if o["id"] == item_id:
            return o
    raise SetupError("board '{}' has no on-board item '{}'".format(layout["id"], item_id))


def onboard_variants(item):
    """[(variant id or None, label, attach)]: an on-board part attaches one way
    (`attach:`), or one of several (`variants:`, e.g. the panels an LCD
    connector takes, or two ways of driving an HDMI connector)."""
    if item.get("variants"):
        return [(v["id"], v.get("label") or v["id"], v["attach"]) for v in item["variants"]]
    if "attach" not in item:
        return []                      # a device without a peripheral model yet (`device:`)
    return [(None, item.get("label") or item["id"], item["attach"])]


def onboard_attach(layout, use):
    """The attach template of an `onboard:` use (its `variant:` when the part has several)."""
    item = onboard_item(layout, use["onboard"])
    variants = onboard_variants(item)
    if not variants:
        raise SetupError("on-board item '{}' ({}) has no peripheral model yet: a design cannot use it".format(
            item["id"], (item.get("device") or {}).get("kind", "?")))
    if len(variants) == 1 and variants[0][0] is None:
        if use.get("variant") is not None:
            raise SetupError("on-board item '{}' has no variants".format(item["id"]))
        return variants[0][2]
    for vid, _label, attach in variants:
        if vid == use.get("variant"):
            return attach
    raise SetupError("on-board item '{}' is used as one of: {} (variant: ...)".format(
        item["id"], ", ".join(v[0] for v in variants)))


def pin_ref(layout, where):
    """`jd.7` -> the bank reference that connector pin is (`pmod_jd[4]`)."""
    conn_id, _, pin = str(where).partition(".")
    c = connector(layout, conn_id)
    pins = c.get("pins") or {}
    for key, ref in pins.items():
        if str(key) == pin:
            return ref
    raise SetupError("connector '{}' has no signal pin '{}'".format(conn_id, pin))


def ref_index(layout):
    """{bank reference: `connector.pin`} over every connector of the layout. A
    pin on several connectors (a Tang Mega 138K Pmod pin is also a J14 pin) is
    the one of the connector of the reference's own bank, else a physical
    (verified) header's rather than a logical row's."""
    out = {}
    conns = layout.get("headers") or []
    for c in sorted(conns, key=lambda c: c.get("type") != "pin_row"):
        for key, ref in (c.get("pins") or {}).items():
            out[ref] = "{}.{}".format(c["id"], key)
    for c in conns:
        for key, ref in (c.get("pins") or {}).items():
            if c.get("bank") and re.match(r"^" + re.escape(c["bank"]) + r"\b", str(ref)):
                out[ref] = "{}.{}".format(c["id"], key)
    return out


def module_header_size(module):
    """N when the module's pins are numbered 1..N (a header it plugs in by),
    else None (a module that is wired pin by pin)."""
    keys = [str(k) for k in module.get("pins") or {}]
    if not keys or not all(k.isdigit() for k in keys):
        return None
    return max(int(k) for k in keys)


def _row_positions(connectors, layout, plug):
    """The connector pin keys under module pins 1..N for a placement
    {connector, row, reversed}; row `all`: a module as big as the connector
    (a 2x6 Pmod), its pins over the rows in order (reversed: turned round)."""
    c = connector(layout, plug["connector"])
    rows = (connectors.get(c["type"]) or {}).get("rows") or []
    if plug.get("row") == "all":
        row = [str(k) for r in rows for k in r]
    else:
        r = int(plug.get("row", 1))
        if not 1 <= r <= len(rows):
            raise SetupError("connector '{}' has no row {}".format(c["id"], r))
        row = [str(k) for k in rows[r - 1]]
    return c, (list(reversed(row)) if plug.get("reversed") else row)


def plug_fits(connectors, layout, module, plug):
    """[] when the module's numbered header fits the placement: as many pins as
    the row, its power / ground pins on the connector's VCC / GND and its
    signal pins on signal pins; else the reasons."""
    n = module_header_size(module)
    c, row = _row_positions(connectors, layout, plug)
    if n is None:
        return ["{} has no numbered header to plug in".format(module.get("name") or module["id"])]
    if n != len(row):
        return ["{} has {} pins, a row of {} has {}".format(module.get("name") or module["id"], n, c["id"], len(row))]
    power = {str(k): v for k, v in ((connectors.get(c["type"]) or {}).get("power") or {}).items()}
    signal = {str(k) for k in c.get("pins") or {}}
    wrong = []
    for k in range(1, n + 1):
        role = module["pins"].get(str(k), module["pins"].get(k))
        at = row[k - 1]
        want = {"power": "VCC", "ground": "GND"}.get(role)
        if want and power.get(at) != want:
            wrong.append("module pin {} ({}) would sit on {} pin {} ({})".format(k, role, c["id"], at, power.get(at, "a signal")))
        elif role and not want and at not in signal:
            wrong.append("module pin {} ({}) would sit on {} pin {} ({})".format(k, role, c["id"], at, power.get(at, "no signal")))
    return wrong


def plug_wires(connectors, layout, module, plug):
    """{module pin: connector.pin} for a module plugged by its numbered header
    into a connector row (plug: {connector, row, reversed})."""
    wrong = plug_fits(connectors, layout, module, plug)
    if wrong:
        raise SetupError("{} does not plug into {} row {}{}: {}".format(
            module.get("name") or module["id"], plug["connector"], plug.get("row", 1),
            " reversed" if plug.get("reversed") else "", "; ".join(wrong)))
    c, row = _row_positions(connectors, layout, plug)
    return {str(k): "{}.{}".format(c["id"], row[int(k) - 1])
            for k, sig in module["pins"].items() if sig not in _PASSIVE}


def plug_placements(connectors, layout, module, conn_ids=None):
    """Every placement a module's numbered header fits, in layout order."""
    out = []
    for c in layout.get("headers") or []:
        if conn_ids is not None and c["id"] not in conn_ids:
            continue
        rows = (connectors.get(c["type"]) or {}).get("rows") or []
        for r in list(range(1, len(rows) + 1)) + (["all"] if len(rows) > 1 else []):
            for rev in (False, True):
                plug = {"connector": c["id"], "row": r}
                if rev:
                    plug["reversed"] = True
                if not plug_fits(connectors, layout, module, plug):
                    out.append(plug)
    return out


# ---------------------------------------------------------------------------
# setup -> configuration
# ---------------------------------------------------------------------------

def _bank_signal(bind, board):
    """The one signal of an on-board part's bind that is a whole bank of
    listed pins (led_bank's `led: onboard_leds`), and that bank: the signal a
    use's `pins:` narrows."""
    whole = [(sig, ref) for sig, ref in bind.items()
             if isinstance(ref, str) and "." not in ref and "[" not in ref and _bank_size(board, ref) is not None]
    if len(whole) != 1:
        raise SetupError("`pins:` needs a part bound to one whole bank of pins, not {}".format(bind))
    return whole[0]


def _bank_size(board, bank):
    """How many pins the bank has when it is a list of pins, else None."""
    pins = ((config_init.peek_board(board) or {}).get("banks") or {}).get(bank, {}).get("pins")
    return len(pins) if isinstance(pins, list) else None


def _subset_pins(t_bind, a_bind, board):
    """The `pins:` a use took of the part's whole bank when the attach's bind is
    the part's with that one signal a list of the bank's pins, else None."""
    try:
        sig, bank = _bank_signal(t_bind, board)
    except SetupError:
        return None
    refs = a_bind.get(sig)
    if not isinstance(refs, list) or set(a_bind) != set(t_bind) or \
            any(a_bind[k] != t_bind[k] for k in t_bind if k != sig):
        return None
    keys = []
    for r in refs:
        m = re.match(r"^" + re.escape(bank) + r"\[(\d+)\]$", str(r))
        if not m:
            return None
        keys.append(int(m.group(1)))
    return keys


def _connector_banks(layout):
    """{bank: [its pin refs in bank order]} for the connectors that are one bank."""
    out = {}
    for c in layout.get("headers") or []:
        if c.get("bank"):
            refs = [str(r) for r in (c.get("pins") or {}).values()]
            idx = {r: int(m.group(1)) for r in refs for m in [re.match(r"^" + re.escape(c["bank"]) + r"\[(\d+)\]$", r)] if m}
            if len(idx) == len(refs) and sorted(idx.values()) == list(range(len(refs))):
                out[c["bank"]] = sorted(refs, key=idx.get)
    return out


def _whole_bank(layout, refs):
    """A module signal wired to every pin of a header bank in bank order is
    that bank (the generated top connects the bank, not its pins one by one)."""
    for bank, pins in _connector_banks(layout).items():
        if [str(r) for r in refs] == pins:
            return bank
    return refs


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
    # the peripheral's own signal order, not the order the wires are listed in (a
    # browser lists a Pmod's pins "1", "2", "4", "9" numerically, whatever the file says)
    rank = {sg["name"]: k for k, sg in enumerate((config_init.read_peripherals().get(module["peripheral"]) or {})
                                                   .get("signals") or [])}
    order.sort(key=lambda n: rank.get(n, len(rank)))
    bind = {}
    for name in order:
        if name in scalars:
            bind[name] = scalars[name]
            continue
        bits = vectors[name]
        if sorted(bits) != list(range(len(bits))):
            raise SetupError("module '{}': {} is not wired bit 0 upwards".format(module["id"], name))
        bind[name] = _whole_bank(layout, [bits[i] for i in range(len(bits))])
    attach = {"peripheral": module["peripheral"]}
    # the module's own parameters (a Pmod's 4 servo channels), the use's over them
    params = dict(module.get("params") or {}, **(use.get("params") or {}))
    if params:
        attach["params"] = copy.deepcopy(params)
    attach["bind"] = bind
    return attach


# a rig's build targets (config/init.py): copied as they are between setup and configuration
TARGET_KEYS = ("toolchain", "toolchains", "part", "parts", "aliases")
# the design section's keys and the configuration keys they become
DESIGN_KEYS = (("reset", "reset"), ("clock", "design_clock"), ("uart_rx", "uart_rx"),
               ("width", "design_width"), ("tie", "tie"))
DESIGN_CFG_KEYS = tuple(c for _k, c in DESIGN_KEYS if c != "tie")
USE_KEYS = ("variant", "plug", "wires", "pins", "params", "bind", "design_bits", "for_toolchain")


def conventions_enabled():
    """The rig's design section and design_bits apply. UNIFPGA_PROFILE=0
    (synthesize.py --no-profile) turns them off: buses concatenated in attach
    order, a power-up reset, no ties from the design section."""
    return os.environ.get("UNIFPGA_PROFILE", "1") not in ("0", "false", "no", "off")


def generate(setup, conventions=None):
    """The configuration dict (the `Configuration:` mapping) for a setup. Its
    `for_toolchain` patches (config/overlay.py; on the setup and on its uses)
    are restated on the configuration and its attaches. `conventions` False
    leaves the design section and the design_bits out (default: the
    UNIFPGA_PROFILE switch)."""
    with config_init.boards_frozen():
        if conventions is None:
            conventions = conventions_enabled()
        tcs = _patched_toolchains(setup, "use")
        cfg = _generate(overlay.select(setup, None, "use"), conventions)
        if not tcs:
            return cfg
        try:
            return overlay.split(cfg, {tc: _generate(overlay.select(setup, tc, "use"), conventions) for tc in tcs},
                                 "attach")
        except ValueError as exc:
            raise SetupError("setup '{}': for_toolchain: {}".format(setup.get("id"), exc))


def _patched_toolchains(doc, list_key):
    """The toolchains a doc's `for_toolchain` patches name, in order."""
    out = list(doc.get("for_toolchain") or {})
    for item in doc.get(list_key) or []:
        for tc in (item.get("for_toolchain") or {}) if isinstance(item, dict) else ():
            if tc not in out:
                out.append(tc)
    return out


def _generate(setup, conventions=True):
    layout = read_drawn(setup["board"])
    modules = read_modules()
    connectors = read_connectors()
    cfg = {"id": setup["id"], "board": setup["board"]}
    for k in TARGET_KEYS:
        if setup.get(k) is not None:
            cfg[k] = copy.deepcopy(setup[k])
    attach = []
    for use in setup.get("use") or []:
        unknown = set(use) - set(USE_KEYS) - {"onboard", "module", "gpio", "raw"}
        if unknown:
            raise SetupError("setup '{}': a use has no key {}: {}".format(
                setup["id"], ", ".join(sorted(unknown)), use_label(use, use.get("raw") or {})))
        if "onboard" in use:
            t = onboard_attach(layout, use)
            # the use's params over the part's; `name: null` leaves one of the part's out
            params = {k: v for k, v in dict(t.get("params") or {}, **(use.get("params") or {})).items()
                      if v is not None}
            a = {"peripheral": t["peripheral"]}
            bind = copy.deepcopy(t["bind"])
            if use.get("pins") is not None:          # some of the part's pins, in this order
                sig, bank = _bank_signal(bind, setup["board"])
                n = _bank_size(setup["board"], bank)
                bad = [k for k in use["pins"] if not (isinstance(k, int) and 0 <= k < n)]
                if bad:
                    raise SetupError("on-board item '{}': {} has pins 0..{}, not {}".format(
                        use["onboard"], bank, n - 1, ", ".join(map(str, bad))))
                bind[sig] = ["{}[{}]".format(bank, k) for k in use["pins"]]
                if "width" in params:
                    params["width"] = len(use["pins"])
            if params:
                a["params"] = params
            a["bind"] = bind
            a = copy.deepcopy(a)
        elif "module" in use:
            a = _module_attach(connectors, layout, modules, use)
        elif "gpio" in use:
            c = connector(layout, use["gpio"])
            if not c.get("bank"):
                raise SetupError("connector '{}' is not one bank: it cannot be the design's gpio".format(c["id"]))
            pid, sig = gpio_passthrough()
            a = {"peripheral": pid}
            if use.get("params"):
                a["params"] = copy.deepcopy(use["params"])
            if use.get("pins") is not None:          # some of its pins, in this order
                missing = [k for k in use["pins"] if str(k) not in (c.get("pins") or {})]
                if missing:
                    raise SetupError("connector '{}' has no pin {}".format(c["id"], ", ".join(map(str, missing))))
                a["bind"] = {sig: [c["pins"][str(k)] for k in use["pins"]]}
            else:
                a["bind"] = {sig: c["bank"]}
        elif "raw" in use:
            a = copy.deepcopy(use["raw"])
        else:
            raise SetupError("setup '{}': a use needs onboard, module, gpio or raw: {}".format(setup["id"], use))
        # how the design sees this part: a signal on other pins or left
        # unwired, the design bits its bits are (both conventions: off with
        # the design section)
        if conventions and use.get("bind") and "raw" not in use:
            b = dict(a.get("bind") or {})
            for sig, ref in use["bind"].items():
                if ref is None:
                    b.pop(sig, None)
                else:
                    b[sig] = copy.deepcopy(ref)
            a["bind"] = b
        if not conventions:
            a.pop("design_bits", None)
        elif use.get("design_bits"):
            a["design_bits"] = copy.deepcopy(use["design_bits"])
        attach.append(a)
    cfg["attach"] = attach
    for k, v in (setup.get("extra") or {}).items():
        if k in DESIGN_CFG_KEYS or k == "attach":
            raise SetupError("setup '{}': extra.{} belongs to the design section".format(setup["id"], k))
        cfg[k] = copy.deepcopy(v)
    if conventions:
        _apply_design_section(setup, cfg)
    return cfg


def _apply_design_section(setup, cfg):
    """The setup's `design:` onto the configuration: reset, the design clock,
    uart_rx, wider buses; its ties over the hardware's."""
    design = setup.get("design") or {}
    known = dict(DESIGN_KEYS)
    unknown = set(design) - set(known)
    if unknown:
        raise SetupError("setup '{}': design: has no key {} (reset, clock, uart_rx, width, tie)".format(
            setup["id"], ", ".join(sorted(unknown))))
    for key, cfg_key in DESIGN_KEYS:
        value = design.get(key)
        if value is None:
            continue
        if key == "tie":
            tie = dict(cfg.get("tie") or {})
            tie.update(value)
            cfg["tie"] = tie
        elif key == "uart_rx":
            cfg["uart_rx"] = int(value)
        elif key == "width":
            cfg["design_width"] = {k: int(v) for k, v in value.items()}
        else:
            cfg[cfg_key] = copy.deepcopy(value)


# ---------------------------------------------------------------------------
# configuration -> setup
# ---------------------------------------------------------------------------

def _params(a):
    return a.get("params") or {}


def _derive_module(a, layout, modules, connectors, refs, prefer=None):
    """A module use for attach `a` when some module of its peripheral covers
    every bound signal and every reference is a connector pin; else None.
    Several modules can make the same attach (an I2S DAC breakout and a
    PmodAMP3): `prefer`, the module the setup already names, wins."""
    ordered = sorted(modules.values(), key=lambda m: m["id"] != prefer)
    for module in ordered:
        if module["peripheral"] != a["peripheral"]:
            continue
        by_signal = {sig: pin for pin, sig in module["pins"].items() if sig not in _PASSIVE}
        wires = {}
        whole = _connector_banks(layout)
        for sig, ref in (a.get("bind") or {}).items():
            if isinstance(ref, str) and ref in whole and "{}[0]".format(sig) in by_signal:
                ref = whole[ref]                 # a whole header bank: its pins, in bank order
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
        base, have = module.get("params") or {}, _params(a)
        if list(dict(base, **have)) != list(have):
            continue                    # parameters the module's defaults cannot reproduce
        use = {"module": module["id"]}
        plug = _as_plug(connectors, layout, module, wires)
        if plug:
            use["plug"] = plug
        else:
            use["wires"] = wires
        own = {k: v for k, v in have.items() if base.get(k) != v}
        if own:
            use["params"] = copy.deepcopy(own)
        return use
    return None


def _as_plug(connectors, layout, module, wires):
    if module_header_size(module) is None:
        return None
    conns = {w.partition(".")[0] for w in wires.values()}
    if len(conns) != 1:
        return None
    for plug in plug_placements(connectors, layout, module, conns):
        if plug_wires(connectors, layout, module, plug) == wires:     # the order of wires does not matter
            return plug
    return None


def derive(configuration, previous=None):
    """The setup that generates `configuration` (a `Configuration:` dict).
    Attaches the physical model does not cover are kept as `raw` uses. A
    configuration does not say which module made an attach several could
    make: the one `previous` (default: the setup of that id) names is kept."""
    if previous is None:
        previous = read_setups().get(configuration.get("id")) or {}
    before = [u.get("module") for u in previous.get("use") or []]
    tcs = _patched_toolchains(configuration, "attach")
    setup = _derive(overlay.select(configuration, None, "attach"), before)
    if not tcs:
        return setup
    return overlay.split(setup, {tc: _derive(overlay.select(configuration, tc, "attach"), before) for tc in tcs}, "use")


def _derive(configuration, before=()):
    layout = read_drawn(configuration["board"])
    modules = read_modules()
    connectors = read_connectors()
    refs = ref_index(layout)
    banks = {c.get("bank"): c["id"] for c in layout.get("headers") or [] if c.get("bank")}
    setup = {"id": configuration["id"], "board": configuration["board"]}
    for k in TARGET_KEYS:
        if configuration.get(k) is not None:
            setup[k] = copy.deepcopy(configuration[k])
    uses = []
    for a_full in configuration.get("attach") or []:
        a = {k: v for k, v in a_full.items() if k != "design_bits"}
        use = None
        for o, vid, t in ((o, vid, t) for o in layout.get("parts") or [] for vid, _l, t in onboard_variants(o)):
            if t["peripheral"] != a["peripheral"] or list(a) != [k for k in ("peripheral", "params", "bind") if k in a]:
                continue
            subset = None
            if ordered(t.get("bind")) != ordered(a.get("bind")):
                subset = _subset_pins(t.get("bind") or {}, a.get("bind") or {}, configuration["board"])
                if subset is None:
                    continue
            if True:
                use = {"onboard": o["id"]}
                if vid is not None:
                    use["variant"] = vid
                base, have = _params(t), _params(a)
                if subset is not None:
                    use["pins"] = subset
                    if "width" in base and have.get("width") == len(subset):
                        have = dict(have, width=base["width"])   # the width `pins:` sets
                if have != base:
                    use["params"] = dict({k: v for k, v in have.items() if base.get(k) != v},
                                         **{k: None for k in base if k not in have})   # null: left out
                merged = {k: v for k, v in dict(base, **use.get("params", {})).items() if v is not None}
                if list(merged) != list(have):
                    use = None                   # the configuration orders its params otherwise
                    continue
                break
        gpio_pid, gpio_sig = gpio_passthrough()
        io = (a.get("bind") or {}).get(gpio_sig)
        if use is None and a["peripheral"] == gpio_pid and isinstance(io, str) and io in banks \
                and set(a["bind"]) == {gpio_sig}:
            use = {"gpio": banks[io]}
            if _params(a):
                use["params"] = copy.deepcopy(a["params"])
        if use is None and a["peripheral"] == gpio_pid and isinstance(io, list) and io and set(a["bind"]) == {gpio_sig}:
            # some pins of one connector: `gpio: <connector>, pins: [...]`
            for c in layout.get("headers") or []:
                keys = {str(ref): k for k, ref in (c.get("pins") or {}).items()}
                if c.get("bank") and all(str(r) in keys for r in io):
                    use = {"gpio": c["id"], "pins": [keys[str(r)] for r in io]}
                    if _params(a):
                        use["params"] = copy.deepcopy(a["params"])
                    break
        if use is None:
            use = _derive_module(a, layout, modules, connectors, refs,
                                 prefer=before[len(uses)] if len(before) == len(configuration.get("attach") or []) else None)
        if use is None:
            use = {"raw": copy.deepcopy(a_full)}
        elif a_full.get("design_bits"):
            use["design_bits"] = copy.deepcopy(a_full["design_bits"])
        uses.append(use)
    notes = file_notes(configuration["id"])
    if notes:
        setup["notes"] = notes
    setup["use"] = uses
    design = {}
    for key, cfg_key in DESIGN_KEYS:
        if cfg_key != "tie" and configuration.get(cfg_key) is not None:
            design[key] = copy.deepcopy(configuration[cfg_key])
    if design:
        setup["design"] = design
    extra = {k: copy.deepcopy(v) for k, v in configuration.items()
             if k not in ("id", "board", "attach") + TARGET_KEYS + DESIGN_CFG_KEYS}
    if extra:
        setup["extra"] = extra
    return setup


_STANDARD_NOTE = re.compile(r"^(Configuration '.*'\.|Generated from config/setups/.*)$")


def file_notes(config_id):
    """The notes of the rig's setup (they head its configuration text)."""
    return list((read_setups().get(config_id) or {}).get("notes") or [])


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
        return use["onboard"] + (" ({})".format(use["variant"]) if use.get("variant") else "")
    if "module" in use:
        return use["module"]
    if "gpio" in use:
        return "gpio {}".format(use["gpio"]) + (" ({} pins)".format(len(use["pins"])) if use.get("pins") is not None else "")
    return "raw {}".format(attach.get("peripheral"))


def _voltage_range(v):
    """`3.3` or `[3.3, 5.0]` -> (low, high); (None, None) when not stated."""
    if v is None:
        return None, None
    if isinstance(v, (list, tuple)):
        return min(v), max(v)
    return v, v


def _target_problems(setup):
    """The rig's toolchains, chips, aliases and per-toolchain patches."""
    problems = []
    known = set(config_init.read_toolchains())
    listed = config_init.rig_toolchains(setup)
    if listed[0] != setup.get("toolchain"):
        problems.append(("error", "toolchains: must start with the default toolchain '{}'".format(setup.get("toolchain"))))
    if len(set(listed)) != len(listed):
        problems.append(("error", "toolchains: names a toolchain twice"))
    for toolchain in [setup.get("toolchain")] + [t for t in listed if t != setup.get("toolchain")]:
        if toolchain not in known:
            problems.append(("error", "unknown toolchain '{}'".format(toolchain)))
            continue
        try:
            ok = config_init.is_compatible(config_init.peek_boards(), config_init.read_chips(),
                                           setup["board"], toolchain)
        except config_init.ConfigError:
            ok = True                       # a board without a chip: reported where it matters
        if not ok:
            problems.append(("error", "toolchain '{}' does not build for {}'s chip".format(toolchain, setup["board"])))
    for toolchain in _patched_toolchains(setup, "use"):
        if toolchain not in listed:
            problems.append(("error", "for_toolchain: {} is not one of its toolchains".format(toolchain)))
    parts = config_init.rig_parts(setup)
    if parts[0] != setup.get("part"):
        problems.append(("error", "parts: must start with the default part '{}'".format(setup.get("part"))))
    others = {sid: s for sid, s in read_setups().items() if sid != setup.get("id")}
    taken = {a: sid for sid, s in others.items() for a in s.get("aliases") or {}}
    if setup.get("id") in taken:
        problems.append(("error", "'{}' is another name of setup {}".format(setup["id"], taken[setup["id"]])))
    for alias, target in (setup.get("aliases") or {}).items():
        target = target or {}
        if alias in others or alias == setup.get("id"):
            problems.append(("error", "alias '{}' is a setup's id".format(alias)))
        elif alias in taken:
            problems.append(("error", "alias '{}' is already another name of setup {}".format(alias, taken[alias])))
        if target.get("toolchain") is not None and target["toolchain"] not in listed:
            problems.append(("error", "alias '{}': toolchain {} is not one of its toolchains".format(alias, target["toolchain"])))
        if target.get("part") is not None and target["part"] not in parts:
            problems.append(("error", "alias '{}': part {} is not one of its parts".format(alias, target["part"])))
    return problems


def validate(setup, clashes=None):
    """[(level, message)]: level 'error' or 'warning'. `clashes`, a list,
    receives (use a, use b, [FPGA pins]) for every two uses wired to the same
    pins (the editor offers to re-wire or remove one of them)."""
    with config_init.boards_frozen():
        problems = []
        pair_pins = {}                       # (earlier use, later use) -> pins both use
        try:
            layout = read_drawn(setup["board"])
            cfg = generate(setup)
        except SetupError as exc:
            return [("error", str(exc))]
        peripherals = config_init.read_peripherals()
        modules = read_modules()
        connectors = read_connectors()
        problems += _target_problems(setup)

        owner = {}
        covered = plugged_row_refs(setup, layout, connectors)
        for ref, k in covered.items():
            for _bit, pin in codegen._bind_pins(layout, ref):
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
            # the design's gpio may share a pin with a part (the generated top connects
            # both; the editor warns): a gpio use, or a raw attach of a peripheral that
            # hands its pins straight to the design
            gpio = "gpio" in use or codegen._is_gpio_passthrough(contract)
            # a driverless part's pins the design does not reach (design_bits: null
            # there) are free for another part — the HEX decimal points on the
            # top LEDs of a Terasic board (codegen's ledger applies the same rule)
            bit_lists = [list(b or []) for b in (a.get("design_bits") or {}).values()] if contract.get("driver") is None else []
            for sig, ref in (a.get("bind") or {}).items():
                for k, (port_bit, pin) in enumerate(codegen._bind_pins(layout, ref)):
                    if bit_lists and any(k >= len(bl) or bl[k] is None for bl in bit_lists):
                        continue
                    if pin is None:
                        bank = (layout.get("banks") or {}).get(re.split(r"[.\[]", str(ref))[0])
                        if not (isinstance(bank, dict) and bank.get("virtual")):     # an on-chip source has no pin
                            problems.append(("error", "{}: {} ({}) is not a pin of the board".format(label, sig, port_bit)))
                        continue
                    for p in str(pin).split(","):
                        prev = owner.get(p)          # (use index, label, is gpio)
                        if prev and prev[0] != n and not (prev[2] or gpio):
                            pair_pins.setdefault((prev[0], n, prev[1], label), []).append(p)
                        owner.setdefault(p, (n, label, gpio))
        for (a, b, la, lb), pins in pair_pins.items():
            problems.append(("error", "{} {} used by both {} and {}".format(
                "pin" if len(pins) == 1 else "pins", ", ".join(pins), la, lb)))
            if clashes is not None:
                clashes.append((a, b, pins))
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
    L = ["# Rig {}: its board, what is on it and how the design sees it.".format(setup["id"]),
         "# The build expands it into the rig's configuration (./unifpga setup show {}).".format(setup["id"]),
         "", "Setup:"]
    for k in ("id", "board") + TARGET_KEYS:
        if setup.get(k) is not None:
            L.append("  {}: {}".format(k, _flow(setup[k]) if isinstance(setup[k], (list, dict)) else _scalar(setup[k])))
    if setup.get("notes"):
        L.append("  notes:")
        L.extend("    - {}".format(_scalar(n)) for n in setup["notes"])
    L.append("  use:")
    for use in setup.get("use") or []:
        head = next(k for k in ("onboard", "module", "gpio", "raw") if k in use)
        if head == "raw":
            L.append("    - raw: {}".format(_flow(use["raw"])))
        else:
            L.append("    - {}: {}".format(head, use[head]))
        for k in USE_KEYS:
            if k in use and (head != "raw" or k in ("for_toolchain", "design_bits")):
                L.append("      {}: {}".format(k, _scalar(use[k]) if k == "variant" else _flow(use[k])))
    import yaml
    for k in ("design", "extra", "for_toolchain"):
        if setup.get(k):
            L.append("  {}:".format(k))
            text = yaml.safe_dump(setup[k], sort_keys=False, width=100, default_flow_style=None)
            L.extend("    " + line for line in text.rstrip("\n").split("\n"))
    return "\n".join(L) + "\n"


def write_setup(setup):
    os.makedirs(SETUP_DIR, exist_ok=True)
    path = os.path.join(SETUP_DIR, setup["id"] + ".yml")
    text = dump_setup(setup)                     # rendered before the file is touched
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


# ---------------------------------------------------------------------------
# configuration files
# ---------------------------------------------------------------------------

_PLAIN = re.compile(r"^[A-Za-z_][\w.\-/ ]*$")
_TOP_COMMENTS = {
    "reset": "What resets the design (the rig's design section)",
    "design_clock": "The clock design_top runs on (the rig's design section)",
    "uart_rx": "What uart_rx reads with no UART pin wired (the rig's design section)",
    "design_width": "Design buses wider than the bits wired to them (the rig's design section)",
    "io_overrides": "Gowin IO_TYPE this configuration states beyond the board-wide ones",
    "tie": "Pins held at a constant or driven from the reset — `assign PIN = 1'b0` / `~ rst`",
    "aliases": "The ids of the per-toolchain / per-chip copies this rig replaced (still accepted)",
    "for_toolchain": "What a toolchain builds differently (config/overlay.py patches, from the setup)",
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
        elif isinstance(v, dict) and v:
            lines.append("{}{}:".format(" " * indent, _scalar(k)))
            _block(lines, indent + 2, v)
        else:
            lines.append("{}{}: {}".format(" " * indent, _scalar(k), _inline(v)))


def emit_configuration(cfg, notes=None):
    """A configuration dict as text (./unifpga setup show; the board editor's
    configuration pane): what the build reads, expanded from the setup."""
    L = ["# Configuration '{}'.".format(cfg["id"])]
    L += ["# " + n for n in (notes or [])]
    L += ["# Expanded from config/setups/{}.yml (./unifpga setup show); edit the setup.".format(cfg["id"]),
          "", "Configuration:"]
    for k in ("id", "board", "toolchain"):
        L.append("  {}: {}".format(k, _scalar(cfg[k])))
    if cfg.get("toolchains") is not None:
        L.append("  toolchains: {}   # every toolchain it is checked with, the default first".format(
            _inline(cfg["toolchains"])))
    if cfg.get("part") is not None:
        L.append("  part: {}   # which of the board's chips this configuration targets".format(_scalar(cfg["part"])))
    if cfg.get("parts") is not None:
        L.append("  parts: {}   # every chip it is checked with, the default first".format(_inline(cfg["parts"])))
    rest = [k for k in cfg if k not in ("id", "board", "attach", "for_toolchain") + TARGET_KEYS[:-1]]
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
    for k in ("tie", "for_toolchain"):
        if k in cfg:
            L += ["", "  # " + _TOP_COMMENTS[k]]
            _block(L, 2, {k: cfg[k]})
    return "\n".join(L) + "\n"


def generated_text(setup):
    return emit_configuration(generate(setup), setup.get("notes"))


# ---------------------------------------------------------------------------
# auto-wiring
# ---------------------------------------------------------------------------

def plugged_row_refs(setup, layout, connectors, skip=None):
    """{bank ref: use index} for every signal pin under a plugged module:
    the module covers its whole row, the pins it leaves unconnected too."""
    out = {}
    for k, use in enumerate(setup.get("use") or []):
        if k == skip or "plug" not in use:
            continue
        c, row = _row_positions(connectors, layout, use["plug"])
        pins = {str(k2): ref for k2, ref in (c.get("pins") or {}).items()}
        for key in row:
            if str(key) in pins:
                out[pins[str(key)]] = k
    return out


def used_pins(setup, skip=None):
    """FPGA pins the setup's uses occupy (all but use `skip`), the pins under
    a plugged module included."""
    board = config_init.peek_board(setup["board"]) or {}
    rest = dict(setup, use=[u for k, u in enumerate(setup.get("use") or []) if k != skip])
    used = set()
    refs = [ref for a in generate(rest)["attach"] for ref in (a.get("bind") or {}).values()]
    refs += list(plugged_row_refs(setup, read_drawn(setup["board"]), read_connectors(), skip=skip))
    for ref in refs:
        for _bit, pin in codegen._bind_pins(board, ref):
            if pin:
                used.update(str(pin).split(","))
    return used


def autowire(setup, index):
    """{"plug": ...} or {"wires": ...} for module use `index`: a module with a
    numbered header plugged into the first free row it fits, any other wired in order to
    the first connector with enough free pins at a voltage it runs at. Pins
    other uses occupy (on-board devices, gpio headers, modules) are avoided."""
    use = setup["use"][index]
    modules, ctypes = read_modules(), read_connectors()
    module = modules.get(use.get("module"))
    if module is None:
        raise SetupError("use {} is not a module".format(index))
    layout = read_drawn(setup["board"])
    used = used_pins(setup, skip=index)
    lo, hi = _voltage_range(module.get("voltage"))
    need = [p for p, sig in module["pins"].items() if sig not in _PASSIVE]

    def free(conn, key):
        ref = (conn.get("pins") or {}).get(key, (conn.get("pins") or {}).get(int(key) if str(key).isdigit() else key))
        if ref is None:
            return False
        pins = [p for _b, p in codegen._bind_pins(layout, ref)]
        return pins and all(p and not set(str(p).split(",")) & used for p in pins)

    candidates = []
    for c in layout.get("headers") or []:
        v = (ctypes.get(c["type"]) or {}).get("voltage")
        if lo is None or v is None or lo <= v <= hi:
            candidates.append(c)
    for plug in plug_placements(ctypes, layout, module, {c["id"] for c in candidates}):
        c = connector(layout, plug["connector"])
        _c, row = _row_positions(ctypes, layout, plug)
        if all(free(c, k) for k in row if k in {str(x) for x in c.get("pins") or {}}):
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

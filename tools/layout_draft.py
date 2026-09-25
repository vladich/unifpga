"""
Board layouts generated from data, for boards nobody has laid out by hand.

A layout (config/layouts/<board>.yml, see tools/setup.py) needs the board's
headers, with which pinmap entry each physical pin is, and its on-board parts
as attaches. draft() builds one from:

  * the board's pinmap: every bank that is not `onboard_*` or a clock and has a
    list of pins is a header;
  * the board's configurations: every attach whose pins are on no header is an
    on-board part (the same peripheral, binding and parameters, so derive()
    turns the configurations back into setups);
  * the board's registry entry (tools/board_sources.py; the registry is a
    repository of its own): a header whose facts
    verify gets its physical numbering and connector type, an on-board part its
    silkscreen label.

A header without verified facts is a logical row (connector type `pin_row`)
whose pins are named by their pinmap index ([0], [1], ...), so nobody reads
them as physical pin numbers. The layout says `verified: true` only when every
header and part in it is backed by verified facts. Hand-made layouts (without
`generated: true`) are never overwritten.
"""

import json
from collections import Counter
import os
import re

import yaml

from config import init as config_init
from tools import board_sources
from tools import codegen
from tools import setup as su

LAYOUT_DIR = os.path.join(su.CONFIG_DIR, "layouts")
_REF_BANK = re.compile(r"^([A-Za-z_]\w*)")


def _banks_of(value):
    """The pinmap banks a bind value names (`onboard_7seg.hex0`, `gpio[3]`, lists)."""
    if isinstance(value, (list, tuple)):
        return {b for v in value for b in _banks_of(v)}
    m = _REF_BANK.match(str(value))
    return {m.group(1)} if m else set()


HEADER_KINDS = ("pmod", "header")


def header_banks(pinmap):
    """The banks that are headers: not on-board, not a clock, a list of pins
    (a bank an inventory tags as some other device is an on-board part)."""
    out = []
    for name, bank in (pinmap.get("pinBanks") or {}).items():
        pins = bank.get("pins") if isinstance(bank, dict) else bank
        if name.startswith("onboard_") or (isinstance(bank, dict) and "frequency_mhz" in bank):
            continue
        if isinstance(bank, dict) and (bank.get("device") or {}).get("kind") not in (None,) + HEADER_KINDS:
            continue
        if isinstance(pins, list) and len(pins) >= 2 and all(isinstance(p, (str, int)) for p in pins):
            out.append(name)
    return out


def _title(bank):
    return re.sub(r"^onboard_", "", bank).replace("_", " ")


def draft(board_id):
    """The layout dict for `board_id` (see the module docstring)."""
    pinmap = config_init.read_board_pinmap(board_id) or {}
    entry = board_sources.read(board_id) or {}
    base_types = config_init._load_yaml(os.path.join(su.CONFIG_DIR, "connectors.yml"), "Connectors") or {}
    connectors_def = dict(base_types, **(entry.get("connector_types") or {}))
    ok_headers, ok_onboard = board_sources.verified_items(board_id, entry or None, connectors_def) if entry else (set(), set())
    facts_h = {h["bank"]: h for h in entry.get("headers") or []}
    facts_o = {o["bank"]: o for o in entry.get("onboard") or []}

    builds = [config_init.for_target(cfg, toolchain)        # every toolchain's build of each rig
              for _cid, cfg in sorted(config_init.read_configurations().items()) if cfg["board"] == board_id
              for toolchain in config_init.rig_toolchains(cfg)]
    # the name configurations give each single FPGA pin (a header pin the board also
    # routes to the microSD slot is `onboard_microsd.dat[1]` where a rig uses it so)
    named = {}
    for cfg in builds:
        for a in cfg.get("attach") or []:
            for value in (a.get("bind") or {}).values():
                for ref in (value if isinstance(value, list) else [value]):
                    pins = [p for _bit, p in codegen._bind_pins(pinmap, ref)] if isinstance(ref, str) else []
                    if len(pins) == 1 and pins[0]:
                        named.setdefault(board_sources._norm_pin(str(pins[0]).split(",")[0]), Counter())[ref] += 1

    connectors = []
    headers = header_banks(pinmap)
    for bank in headers:
        refs = [ref for ref, _pin in board_sources.bank_pins(pinmap, bank)]
        fact = facts_h.get(bank)
        if bank in ok_headers:
            by_pin = {fpga: ref for ref, fpga in board_sources.bank_pins(pinmap, bank)}
            pins = {}
            for phys, pin in fact["pins"].items():
                fpga = board_sources._norm_pin(pin)
                ref = by_pin.get(fpga)
                if ref and named.get(fpga):
                    ref = named[fpga].most_common(1)[0][0]
                if ref:
                    pins[phys] = ref
            c = {"id": fact.get("id") or bank, "type": fact["type"], "label": fact.get("label") or bank.upper(),
                 "bank": bank, "pins": dict(sorted(pins.items(), key=lambda kv: _natural(kv[0])))}
        else:
            c = {"id": bank, "type": "pin_row", "label": bank.upper(),
                 "note": "pins in the pinmap's order: the header's physical pin numbers are not verified yet",
                 "bank": bank, "pins": {"[{}]".format(k): ref for k, ref in enumerate(refs)}}
        connectors.append(c)

    # an on-board part's pins a module is soldered to (a TM1638's DIO on the
    # Colorlight 5A-75B's button pin): a small connector of those pins, so the
    # module is wired like any other; only for a module that also uses header pins
    module_peripherals = {m["peripheral"] for m in su.read_modules().values()}
    pads = {}
    for cfg in builds:
        for a in cfg.get("attach") or []:
            if a["peripheral"] not in module_peripherals:
                continue
            refs = [r for v in (a.get("bind") or {}).values() for r in (v if isinstance(v, list) else [v])
                    if isinstance(r, str)]
            if not any(_banks_of(r) & set(headers) for r in refs):
                continue
            for r in refs:
                bank = sorted(_banks_of(r))[0]
                if bank not in headers and not any(r in c["pins"].values() for c in connectors):
                    pads.setdefault(bank, [])
                    if r not in pads[bank]:
                        pads[bank].append(r)
    for bank, refs in sorted(pads.items()):
        connectors.append({"id": bank + "_pads", "type": "pin_row", "label": _title(bank).upper() + " PADS",
                           "note": "pins of the on-board " + _title(bank) + ", reached by a wire soldered to them",
                           "bank": None, "pins": {"[{}]".format(k): r for k, r in enumerate(refs)}})

    # one part per physical pin group (its first bank), the ways configurations
    # attach it as its variants
    parts, order = {}, []
    for cfg in builds:
        for a in cfg.get("attach") or []:
            # (a gpio passthrough on an on-board device's pins is that device handed
            # to the design's gpio: one of its variants, like any other attach)
            if set(a) - {"peripheral", "params", "bind"}:
                continue
            banks = _banks_of(list((a.get("bind") or {}).values()))
            if not banks or banks & set(headers):
                continue                              # a part on a header: a module or a raw use
            main = sorted(banks)[0]
            attach = {"peripheral": a["peripheral"]}
            if a.get("params") is not None:
                attach["params"] = a["params"]
            attach["bind"] = a.get("bind") or {}
            if main not in parts:
                parts[main] = []
                order.append(main)
            if not any(su.ordered(x) == su.ordered(attach) or
                       (x["peripheral"] == attach["peripheral"] and su.ordered(x["bind"]) == su.ordered(attach["bind"]))
                       for x in parts[main]):
                parts[main].append(attach)
    # a device the board data describes is attached as its model says: the model
    # replaces a configuration's attach of the same peripheral (a better model,
    # model_params, reaches the rigs), attaches of other peripherals stay variants
    banks = pinmap.get("pinBanks") or {}
    for main in order:
        spec = banks.get(main)
        dev = spec.get("device") if isinstance(spec, dict) else None
        model = _model(dev.get("kind"), main, spec) if dev else None
        if model:
            kept = [x for x in parts[main] if x["peripheral"] != model["peripheral"]]
            if len(kept) < len(parts[main]):
                parts[main] = [model] + kept
    onboard = []
    for main in order:
        oid = re.sub(r"^onboard_", "", main)
        fact = facts_o.get(main)
        dev = (banks.get(main) or {}).get("device") if isinstance(banks.get(main), dict) else None
        label = fact.get("label") if main in ok_onboard and fact and fact.get("label") else \
            (dev or {}).get("name") or _title(main)
        # variants in a canonical order, so their ids follow what they are and not
        # which configuration happened to be read first
        attaches = sorted(parts[main], key=lambda x: (x["peripheral"], json.dumps(x, sort_keys=True)))
        if len(attaches) == 1:
            onboard.append({"id": oid, "label": label, "attach": attaches[0]})
            continue
        variants, used = [], set()
        for x in attaches:
            base = "gpio" if x["peripheral"] == su.gpio_passthrough()[0] else x["peripheral"]
            vid, k = base, 2
            while vid in used:
                vid, k = "{}_{}".format(base, k), k + 1
            used.add(vid)
            variants.append({"id": vid, "label": _variant_label(x, [y for y in attaches if y is not x]), "attach": x})
        onboard.append({"id": oid, "label": label, "variants": variants})

    # the board's other devices (a board inventory, tools/inventory.py, tags
    # their banks `device:`): a part attaching the peripheral that models
    # its kind, else a part drawn with its pins that designs cannot use yet
    attached = {b for main in parts for a in parts[main] for b in _banks_of(list(a["bind"].values()))}
    ids = {o["id"] for o in onboard}
    for bank, spec in (pinmap.get("pinBanks") or {}).items():
        dev = spec.get("device") if isinstance(spec, dict) else None
        if not dev or bank in headers or bank in attached:
            continue
        oid = re.sub(r"^onboard_", "", bank)
        while oid in ids:
            oid += "_"
        ids.add(oid)
        attach = _model(dev.get("kind"), bank, spec)
        part = {"id": oid, "label": dev.get("name") or _title(bank)}
        onboard.append(dict(part, attach=attach) if attach else dict(part, device={"kind": dev.get("kind"), "bank": bank}))

    real = [c["bank"] for c in connectors if c.get("bank")]          # pads are not headers to verify
    everything = real + [_main(o) for o in onboard if "device" not in o]
    verified = bool(everything) and all(b in ok_headers for b in real) and \
        all(_main(o) in ok_onboard for o in onboard)
    # the types the registry defines travel with the layout: builds and the
    # editor never read the registry
    own = {c["type"]: connectors_def[c["type"]] for c in connectors if c["type"] not in base_types}
    return {"board": board_id, "verified": verified, "generated": True, "connector_types": own,
            "connectors": connectors, "onboard": onboard}


def _model(kind, bank, spec):
    """The attach of the peripheral whose `models:` names this device kind
    (`kind:` one kind or a list), or None. How the bank's pins meet its signals:

      * `signal: s` — s is a bus: a bank that is one list of pins, or every pin of
        a bank of named pins in order (led_bank for leds, button_array for buttons);
      * `pins: {s: [names]}` — each signal to the first of its names the bank has,
        a name may take a slice of a bus (`LCD_DATA[4:8]`); a signal that is not
        optional must be found;
      * neither — the bank's pins named as the signals (rgb_led's r / g / b).

    A bank of hard-processor pins (`fabric: false`) is never modelled."""
    if spec.get("fabric") is False:
        return None
    pins = spec.get("pins")
    low = str(spec.get("active") or "").split(" ")[0] == "low"
    for pid, p in sorted(config_init.read_peripherals().items()):
        m = p.get("models") or {}
        kinds = m.get("kind") if isinstance(m.get("kind"), list) else [m.get("kind")]
        if kind not in kinds:
            continue
        # `device:` narrows a kind to named chips (an ADXL345, not any accelerometer)
        name = str((spec.get("device") or {}).get("name") or "").lower()
        if m.get("device") and not any(str(d).lower() in name for d in m["device"]):
            continue
        params_def = p.get("parameters") or {}
        signals = p.get("signals") or []
        bind, width = {}, None
        if m.get("signal"):
            if isinstance(pins, list):
                bind[m["signal"]], width = bank, len(pins)
            elif isinstance(pins, dict):
                refs = [r for k, v in pins.items() for r in
                        (["{}.{}[{}]".format(bank, k, i) for i in range(len(v))] if isinstance(v, list)
                         else ["{}.{}".format(bank, k)])]
                bind[m["signal"]] = refs[0] if len(refs) == 1 and len(pins) == 1 and not isinstance(
                    next(iter(pins.values())), list) else refs
                if len(pins) == 1 and isinstance(next(iter(pins.values())), list):
                    bind[m["signal"]] = "{}.{}".format(bank, next(iter(pins)))     # the one bus, whole
                width = len(refs)
            else:
                continue
        elif m.get("pins"):
            if not isinstance(pins, dict):
                continue
            by_name = {str(k).lower(): k for k in pins}
            for sig in signals:
                for name in m["pins"].get(sig["name"]) or []:
                    base, _, rest = str(name).partition("[")
                    key = by_name.get(base.lower())
                    if key is None:
                        continue
                    if rest:                               # a slice of a bus: name[a:b]
                        a, b = (int(x) for x in rest.rstrip("]").split(":"))
                        if not isinstance(pins[key], list) or len(pins[key]) < b:
                            continue
                        bind[sig["name"]] = ["{}.{}[{}]".format(bank, key, i) for i in range(a, b)]
                    else:
                        bind[sig["name"]] = "{}.{}".format(bank, key)
                    break
            if not bind or any(not sg.get("optional") and sg["name"] not in bind for sg in signals):
                continue
            # buses of one length (an XADC's p and n pins): the part's width
            lens = {len(v) if isinstance(v, list) else len(pins[v.split(".", 1)[1]])
                    for v in bind.values() if isinstance(v, list) or isinstance(pins.get(v.split(".", 1)[1]), list)}
            width = lens.pop() if len(lens) == 1 else None
        elif isinstance(pins, dict) and set(pins) == {sg["name"] for sg in signals}:
            bind = {sg["name"]: "{}.{}".format(bank, sg["name"]) for sg in signals}
            buses = [v for v in pins.values() if isinstance(v, list)]
            width = len(buses[0]) if len(buses) == 1 else None
        else:
            continue
        params = {}
        if "width" in params_def and width is not None:
            params["width"] = width
        if low and "active" in params_def:
            params["active"] = "low"
        # what the board data knows of the chip (a PT8211 DAC: LSB-justified)
        params.update({k: v for k, v in (spec.get("model_params") or {}).items() if k in params_def})
        attach = {"peripheral": pid}
        if params:
            attach["params"] = params
        attach["bind"] = bind
        return attach
    return None


def _variant_label(x, others):
    """A variant's name: its peripheral, and what sets it apart from another
    variant of the same peripheral (parameter values, pins, their order)."""
    same = [y for y in others if y["peripheral"] == x["peripheral"]]
    if x["peripheral"] == su.gpio_passthrough()[0] and not same:
        return "its pins as the design's gpio"
    if not same:
        return x["peripheral"]
    y = same[0]
    px, py = x.get("params") or {}, y.get("params") or {}
    diff = ["{}={}".format(k, _flow(px[k])) for k in px if px.get(k) != py.get(k)]
    if not diff and set(x["bind"]) != set(y["bind"]):
        diff = ["pins " + ", ".join(sorted(set(x["bind"]) - set(y["bind"])) or ["fewer"])]
    if not diff and x["bind"] != y["bind"]:
        diff = ["other pins"]
    if not diff:
        diff = ["pins listed in the order " + ", ".join(x["bind"])]
    return "{} ({})".format(x["peripheral"], "; ".join(diff))


def _main(o):
    attach = o["attach"] if "attach" in o else o["variants"][0]["attach"]
    return sorted(_banks_of(list(attach["bind"].values())))[0]


def _natural(key):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", str(key))]


def _flow(value):
    text = yaml.safe_dump(value, default_flow_style=True, sort_keys=False, width=1 << 20, allow_unicode=True)
    return re.sub(r"\n\.\.\.\s*$", "", text).strip()       # a bare scalar ends its document with `...`


def emit(layout):
    """The layout as config/layouts/<board>.yml text."""
    out = ["# Layout of {} generated by tools/layout_draft.py from its pinmap, its".format(layout["board"]),
           "# configurations and its board-sources registry entry ({}.yml); regenerate it with".format(layout["board"]),
           "# ./unifpga layout draft {} (edit the registry, not this file).".format(layout["board"]),
           "# A header whose facts do not verify is a logical row: its pins are named by",
           "# their pinmap index, not by physical pin numbers.", "",
           "Layout:", "  board: {}".format(layout["board"]),
           "  verified: {}".format("true" if layout["verified"] else "false"),
           "  generated: true", ""]
    if layout.get("connector_types"):
        out.append("  connector_types:")
        for tid, ctype in layout["connector_types"].items():
            out.append("    {}:".format(tid))
            out += ["      {}: {}".format(k, _flow(v)) for k, v in ctype.items()]
        out.append("")
    out.append("  connectors:" + ("" if layout["connectors"] else " []"))
    for c in layout["connectors"]:
        out += ["    - id: {}".format(c["id"]), "      type: {}".format(c["type"]),
                "      label: {}".format(_flow(c["label"]))] + \
               (["      note: {}".format(_flow(c["note"]))] if c.get("note") else []) + \
               (["      bank: {}".format(c["bank"])] if c.get("bank") else []) + [
                "      pins: {}".format(_flow({str(k): v for k, v in c["pins"].items()}))]
    out += ["", "  onboard:" + ("" if layout["onboard"] else " []")]
    for o in layout["onboard"]:
        out += ["    - id: {}".format(o["id"]), "      label: {}".format(_flow(o["label"]))]
        if "device" in o:
            out.append("      device: {}   # no peripheral model yet".format(_flow(o["device"])))
            continue
        if "attach" in o:
            out.append("      attach: {}".format(_flow(o["attach"])))
            continue
        out.append("      variants:")
        for v in o["variants"]:
            out += ["        - id: {}".format(v["id"]), "          label: {}".format(_flow(v["label"])),
                    "          attach: {}".format(_flow(v["attach"]))]
    return "\n".join(out) + "\n"


def is_generated(board_id):
    """True when config/layouts/<board>.yml is missing or was generated."""
    layout = su.read_layouts().get(board_id)
    return layout is None or bool(layout.get("generated"))


def write(board_id):
    if not is_generated(board_id):
        raise su.SetupError("config/layouts/{}.yml is hand-made; the drafter does not overwrite it".format(board_id))
    path = os.path.join(LAYOUT_DIR, board_id + ".yml")
    text = emit(draft(board_id))
    old = open(path, encoding="utf-8").read() if os.path.exists(path) else None
    if old != text:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    return path, old != text

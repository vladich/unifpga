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

import os
import re

import yaml

from config import init as config_init
from tools import board_sources
from tools import setup as su

LAYOUT_DIR = os.path.join(su.CONFIG_DIR, "layouts")
_REF_BANK = re.compile(r"^([A-Za-z_]\w*)")


def _banks_of(value):
    """The pinmap banks a bind value names (`onboard_7seg.hex0`, `gpio[3]`, lists)."""
    if isinstance(value, (list, tuple)):
        return {b for v in value for b in _banks_of(v)}
    m = _REF_BANK.match(str(value))
    return {m.group(1)} if m else set()


def header_banks(pinmap):
    """The banks that are headers: not on-board, not a clock, a list of pins."""
    out = []
    for name, bank in (pinmap.get("pinBanks") or {}).items():
        pins = bank.get("pins") if isinstance(bank, dict) else bank
        if name.startswith("onboard_") or (isinstance(bank, dict) and "frequency_mhz" in bank):
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

    connectors = []
    headers = header_banks(pinmap)
    for bank in headers:
        refs = [ref for ref, _pin in board_sources.bank_pins(pinmap, bank)]
        fact = facts_h.get(bank)
        if bank in ok_headers:
            by_pin = {fpga: ref for ref, fpga in board_sources.bank_pins(pinmap, bank)}
            pins = {}
            for phys, pin in fact["pins"].items():
                ref = by_pin.get(board_sources._norm_pin(pin))
                if ref:
                    pins[phys] = ref
            c = {"id": fact.get("id") or bank, "type": fact["type"], "label": fact.get("label") or bank.upper(),
                 "bank": bank, "pins": dict(sorted(pins.items(), key=lambda kv: _natural(kv[0])))}
        else:
            c = {"id": bank, "type": "pin_row", "label": bank.upper() + " (pin order from the pinmap)",
                 "bank": bank, "pins": {"[{}]".format(k): ref for k, ref in enumerate(refs)}}
        connectors.append(c)

    # one part per physical pin group (its first bank), the ways configurations
    # attach it as its variants
    parts, order = {}, []
    for cid, cfg in sorted(config_init.read_configurations().items()):
        if cfg["board"] != board_id:
            continue
        for a in cfg.get("attach") or []:
            if a["peripheral"] == su.gpio_passthrough()[0] or set(a) - {"peripheral", "params", "bind"}:
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
    onboard = []
    for main in order:
        oid = re.sub(r"^onboard_", "", main)
        fact = facts_o.get(main)
        label = fact.get("label") if main in ok_onboard and fact and fact.get("label") else _title(main)
        attaches = parts[main]
        if len(attaches) == 1:
            onboard.append({"id": oid, "label": label, "attach": attaches[0]})
            continue
        variants, used = [], set()
        for x in attaches:
            vid, k = x["peripheral"], 2
            while vid in used:
                vid, k = "{}_{}".format(x["peripheral"], k), k + 1
            used.add(vid)
            variants.append({"id": vid, "label": _variant_label(x, [y for y in attaches if y is not x]), "attach": x})
        onboard.append({"id": oid, "label": label, "variants": variants})

    everything = [c["bank"] for c in connectors] + [_main(o) for o in onboard]
    verified = bool(everything) and all(b in ok_headers for b in [c["bank"] for c in connectors]) and \
        all(_main(o) in ok_onboard for o in onboard)
    # the types the registry defines travel with the layout: builds and the
    # editor never read the registry
    own = {c["type"]: connectors_def[c["type"]] for c in connectors if c["type"] not in base_types}
    return {"board": board_id, "verified": verified, "generated": True, "connector_types": own,
            "connectors": connectors, "onboard": onboard}


def _variant_label(x, others):
    """A variant's name: its peripheral, and what sets it apart from another
    variant of the same peripheral (parameter values, pins, their order)."""
    same = [y for y in others if y["peripheral"] == x["peripheral"]]
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
                "      label: {}".format(_flow(c["label"])), "      bank: {}".format(c["bank"]),
                "      pins: {}".format(_flow({str(k): v for k, v in c["pins"].items()}))]
    out += ["", "  onboard:" + ("" if layout["onboard"] else " []")]
    for o in layout["onboard"]:
        out += ["    - id: {}".format(o["id"]), "      label: {}".format(_flow(o["label"]))]
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

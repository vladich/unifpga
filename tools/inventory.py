"""
Board inventories: every on-board device and header of a board, with its
FPGA pins and the documents they come from, imported into

  * the board's pinmap: a bank per device the pinmap does not have yet,
    tagged `device: {name, kind}` (and `shares:` when other banks use the
    same FPGA balls: pins the board multiplexes between devices);
  * the board catalogue: the producer's product name and a line of main
    characteristics (`ProductName:`, `Summary:`);
  * the board-sources registry entry (tools/board_sources.py): the documents
    and the inventory itself, as the evidence.

Existing banks are never changed: configurations use them, and their output
must stay what it is. Where an inventory disagrees with an existing bank the
report says so, for a person to decide.

An inventory (research output, one YAML document per board):

    board: tang_mega_138k
    product_name: Tang Mega 138K Dock
    summary: GW5AST-138 (138,240 LUT4), 1 GB DDR3, HDMI RX/TX, ...
    documents: [{id, title, kind, url, sha256, bytes, retrieved}, ...]
    devices:
      - name: OV5640 camera (DVP)
        kind: camera_dvp
        bank: onboard_camera
        pins: {pwdn: AB22, db: [P14, P15, ...], ...}   # or {"1": .., "2": ..} for a header
        iostandard: LVCMOS33       # optional, as are active, frequency_mhz
        shares_pins_with: [...]
        source: {doc: <document id>, where: <section / page>}
"""

import json
import os
import re

import yaml

from config import init as config_init
from tools import board_sources

PIN_TOKEN = re.compile(r"^[A-Za-z0-9_]+$")
HEADER_KINDS = {"pmod", "header"}
POWER = re.compile(r"^(GND|AGND|DGND|VCC\w*|VDD\w*|VIO\w*|V\d\w*|\d+V\d*|3V3|5V|NC|N/C)$", re.I)


class InventoryError(Exception):
    pass


def read(path):
    with open(path, encoding="utf-8") as f:
        inv = yaml.safe_load(f)
    if not isinstance(inv, dict) or not inv.get("board") or not isinstance(inv.get("devices"), list):
        raise InventoryError("{}: not an inventory (board, devices)".format(path))
    return inv


# ---------------------------------------------------------------------------
# pins
# ---------------------------------------------------------------------------

def _balls(pins):
    """The FPGA balls of a pins value, in order (a differential "P,N" gives both)."""
    if pins is None:
        return []
    if isinstance(pins, (str, int)):
        return [b for b in str(pins).split(",") if b]
    if isinstance(pins, list):
        return [b for p in pins for b in _balls(p)]
    if isinstance(pins, dict):
        return [b for p in pins.values() for b in _balls(p)]
    return []


def _well_formed(pins):
    return all(PIN_TOKEN.match(b) for b in _balls(pins)) and bool(_balls(pins))


def _as_bank_pins(device):
    """The pins as a pinmap bank holds them: a header's physically numbered
    pins as a list in pin-number order (the pinmap's convention for Pmods and
    headers), everything else as the inventory gives it."""
    pins = device["pins"]
    if device.get("kind") in HEADER_KINDS and isinstance(pins, dict) and all(str(k).isdigit() for k in pins):
        # a header's power and ground positions are not FPGA pins, nor is a
        # name the header repeats (a rail on several positions)
        vals = [str(pins[k]) for k in sorted(pins, key=lambda k: int(k))]
        return [v for v in vals if not POWER.match(v) and vals.count(v) == 1]
    return _stringify(pins)


def _stringify(pins):
    if isinstance(pins, list):
        return [_stringify(p) for p in pins]
    if isinstance(pins, dict):
        return {str(k): _stringify(v) for k, v in pins.items()}
    return str(pins)


def _bank_pins(bank):
    return bank.get("pins") if isinstance(bank, dict) else bank


# ---------------------------------------------------------------------------
# pinmap
# ---------------------------------------------------------------------------

def _pinmap_path(board_id):
    entry = config_init.read_board_entry(board_id)
    if entry is None:
        raise InventoryError("board '{}' is not in the catalogue".format(board_id))
    return os.path.join(config_init.dir_path, "boards", entry["_producer_dir"], entry["_family_dir"], board_id + ".yml")


def plan(inv, pinmap):
    """What importing `inv` into `pinmap` does:
    {add: [(bank, device)], matched: [bank], same_pins_other_order: [bank],
     differs: [(bank, detail)], skipped: [(device name, why)]}."""
    banks = dict((pinmap or {}).get("pinBanks") or {})
    out = {"add": [], "matched": [], "same_pins_other_order": [], "differs": [], "skipped": []}
    taken = set(banks)
    for d in inv["devices"]:
        name = d.get("name") or "?"
        if not isinstance(d.get("pins"), (dict, list, str)) or not _well_formed(d["pins"]):
            out["skipped"].append((name, "no well-formed pins"))
            continue
        bank = d.get("bank") or re.sub(r"\W+", "_", name.lower()).strip("_")
        if d.get("kind") not in HEADER_KINDS and not bank.startswith("onboard_") and bank not in banks:
            bank = "onboard_" + bank                   # an on-board device, never read as a header
        if len(bank) > 40 and bank not in banks:
            bank = ("onboard_" if d.get("kind") not in HEADER_KINDS else "") + re.sub(r"\W+", "_", str(d.get("kind") or "device"))
        balls = _balls(_as_bank_pins(d))
        if bank in banks:
            have = _balls(_bank_pins(banks[bank]))
            if have == balls:
                out["matched"].append(bank)
            elif sorted(have) == sorted(balls):
                out["same_pins_other_order"].append(bank)
            else:
                out["differs"].append((bank, "pinmap {} vs inventory {} ({})".format(
                    have, balls, name)))
            continue
        # the same pins already under another name: not a new device bank
        same = [b for b, v in banks.items() if sorted(_balls(_bank_pins(v))) == sorted(balls)]
        if same:
            out["matched"].append(same[0])
            continue
        if bank in taken:
            k = 2
            while "{}_{}".format(bank, k) in taken:
                k += 1
            bank = "{}_{}".format(bank, k)
        taken.add(bank)
        out["add"].append((bank, d))
    return out


def _shares(bank, balls, all_banks):
    s = set(balls)
    return sorted(b for b, v in all_banks.items() if b != bank and s & set(_bank_pins_balls(v)))


def _bank_pins_balls(v):
    return _balls(_bank_pins(v)) if not isinstance(v, tuple) else v[1]


def _flow(value):
    return json.dumps(value, ensure_ascii=False)          # JSON is valid YAML flow style


def _bank_text(bank, d, shares):
    lines = ["    {}:   # {}".format(bank, d.get("name", "").replace("\n", " "))]
    lines.append("      device: {}".format(_flow({"name": d.get("name"), "kind": d.get("kind")})))
    for k in ("active", "iostandard", "frequency_mhz"):
        if d.get(k) not in (None, ""):
            lines.append("      {}: {}".format(k, _flow(d[k])))
    if shares:
        lines.append("      shares: {}   # the board multiplexes these pins".format(_flow(shares)))
    lines.append("      pins: {}".format(_flow(_as_bank_pins(d))))
    return lines


def _new_pinmap(board_id, path):
    """A pinmap for a board that has none: its header from the catalogue,
    an empty pinBanks the import then fills with every device."""
    entry = config_init.read_board_entry(board_id)
    chip = entry.get("Chip") or ((entry.get("Chips") or [{}])[0].get("Chip") if entry.get("Chips") else None)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join([
            "# {} pin map, from its board inventory (tools/inventory.py; the sources are in".format(board_id),
            "# the board-sources registry entry).", "",
            "Board:", "  id: {}".format(board_id), "  fpga:",
            "    producer: {}".format(_flow(entry.get("PartProducer"))),
            "    family: {}".format(_flow(entry.get("PartFamily"))),
            "    part: {}".format(_flow(chip)),
            "  pinBanks:", ""]))


def _insert_banks(path, text_lines):
    """Append bank lines at the end of the pinmap's `pinBanks:` block."""
    with open(path, encoding="utf-8") as f:
        lines = f.read().split("\n")
    start = next((i for i, l in enumerate(lines) if re.match(r"^  pinBanks:\s*(#.*)?$", l)), None)
    if start is None:
        raise InventoryError("{}: no pinBanks block".format(path))
    end = start + 1
    last = start
    while end < len(lines):
        l = lines[end]
        if l.strip() and not l.startswith("   ") and not l.lstrip().startswith("#"):
            break                                      # the next key of Board (indent 2) or the document's end
        if l.strip():
            last = end
        end += 1
    lines[last + 1:last + 1] = text_lines
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ---------------------------------------------------------------------------
# catalogue
# ---------------------------------------------------------------------------

def _set_product(board_id, product_name, summary):
    """ProductName / Summary on the board's catalogue entry (inserted after
    BoardName, or replaced). Returns True when the file changed."""
    entry = config_init.read_board_entry(board_id)
    path = entry["_catalog_path"]
    with open(path, encoding="utf-8") as f:
        lines = f.read().split("\n")
    i = next(k for k, l in enumerate(lines) if re.match(r"^  - Id:\s*{}\s*$".format(re.escape(board_id)), l))
    j = i + 1
    while j < len(lines) and (lines[j].startswith("    ") or not lines[j].strip()) and not lines[j].startswith("  - "):
        j += 1
    block = lines[i:j]
    block = [l for l in block if not re.match(r"^    (ProductName|Summary):", l)]
    at = next((k for k, l in enumerate(block) if re.match(r"^    BoardName:", l)), 0) + 1
    new = []
    if product_name:
        new.append("    ProductName: {}".format(_flow(product_name)))
    if summary:
        new.append("    Summary: {}".format(_flow(summary)))
    block[at:at] = new
    if block == lines[i:j]:
        return False
    lines[i:j] = block
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return True


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------

def _record(inv):
    """The inventory's documents and devices in the board-sources registry
    entry (documents merged by URL; the inventory replaced)."""
    path = board_sources.registry_path(inv["board"])
    doc = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
    entry = doc.setdefault("BoardSources", {"board": inv["board"]})
    docs = entry.setdefault("documents", [])
    urls = {d.get("url") for d in docs}
    for d in inv.get("documents") or []:
        if d.get("url") not in urls:
            docs.append(d)
    for k in ("product_name", "summary"):
        if inv.get(k):
            entry[k] = inv[k]
    entry["inventory"] = inv["devices"]
    if inv.get("notes"):
        entry["inventory_notes"] = inv["notes"]
    head = ("# Board sources of {b}: the documents its physical model is checked against\n"
            "# and the facts read from them (tools/board_sources.py; the inventory from\n"
            "# tools/inventory.py).\n\n").format(b=inv["board"])
    with open(path, "w", encoding="utf-8") as f:
        f.write(head + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=120))
    return path


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------

def import_inventory(inv, write=True):
    """Import one inventory; the report of what it did (see plan())."""
    board_id = inv["board"]
    path = _pinmap_path(board_id)
    created = False
    if not os.path.exists(path):
        if not write:
            pinmap = {}
        else:
            _new_pinmap(board_id, path)
            config_init.clear_cache()
            created = True
    pinmap = config_init.read_board_pinmap(board_id) or {}
    p = plan(inv, pinmap)
    banks = dict(pinmap.get("pinBanks") or {})
    new = {b: (b, _balls(_as_bank_pins(d))) for b, d in p["add"]}
    everything = dict(banks, **new)
    text = []
    for b, d in p["add"]:
        text += _bank_text(b, d, _shares(b, new[b][1], everything))
    report = dict(p, board=board_id, pinmap=os.path.relpath(path, os.path.dirname(config_init.dir_path)), created=created)
    if created and not p["add"]:
        os.remove(path)                                # nothing with pins: no pinmap after all
        raise InventoryError("board '{}': the inventory has no device with pins".format(board_id))
    if write:
        if text:
            _insert_banks(path, text)
        report["catalogue"] = _set_product(board_id, inv.get("product_name"), inv.get("summary"))
        report["registry"] = _record(inv)
        config_init.clear_cache()
    return report


def report_text(r):
    out = ["{}: {} bank(s) added, {} matched, {} same pins in another order, {} differ, {} skipped".format(
        r["board"], len(r["add"]), len(r["matched"]), len(r["same_pins_other_order"]), len(r["differs"]),
        len(r["skipped"]))]
    out += ["  + {} ({}, {})".format(b, d.get("kind"), d.get("name")) for b, d in r["add"]]
    out += ["  ~ {}: same pins, another order".format(b) for b in r["same_pins_other_order"]]
    out += ["  ! {}: {}".format(b, why) for b, why in r["differs"]]
    out += ["  - {}: {}".format(n, why) for n, why in r["skipped"]]
    return "\n".join(out)

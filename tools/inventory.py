"""
Board inventories: every on-board device and header of a board, with its
FPGA pins and the documents they come from, imported into

  * the board's banks: a bank per device the board does not have yet,
    tagged `device: {name, kind}` (and `shares:` when other banks use the
    same FPGA balls: pins the board multiplexes between devices);
  * the board catalogue: the producer's product name and a line of main
    characteristics (`ProductName:`, `Summary:`);
  * the board file's `documents:` (tools/board_sources.py), merged by URL;
    each new bank carries the inventory item's `source` (which document,
    where), the evidence.

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

PIN_TOKEN = re.compile(r"^[A-Za-z0-9_]+$")
HEADER_KINDS = {"pmod", "header"}
# a header's power / ground positions (never a ball name: V16 is a ball, 3V3 is a rail)
POWER = re.compile(r"^(GND|AGND|DGND|VCC\w*|VDD\w*|VIO\w*|VBUS|VIN|\d+V\d*|3V3|5V|NC|N/C)$", re.I)


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
    """The pins as a bank holds them: a header's physically numbered
    pins as a list in pin-number order (the boards' convention for Pmods and
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
# the board's banks
# ---------------------------------------------------------------------------

def _board_path(board_id):
    """The board's file, config/boards/<producer>/<family>/<id>.yml."""
    board = config_init.read_board(board_id)
    if board is None:
        raise InventoryError("board '{}' is not in the catalogue".format(board_id))
    return board["_path"]


def plan(inv, board):
    """What importing `inv` into the board does:
    {add: [(bank, device)], matched: [bank], same_pins_other_order: [bank],
     differs: [(bank, detail)], skipped: [(device name, why)]}."""
    banks = dict((board or {}).get("banks") or {})
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
                out["differs"].append((bank, "board {} vs inventory {} ({})".format(
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
    src = d.get("source")
    if isinstance(src, dict) and src.get("doc") and src.get("where"):
        lines.append("      source: {}".format(_flow({"doc": src["doc"], "where": src["where"]})))
    return lines


def _ensure_banks(path):
    """A `banks:` block in the board file for a board that has none yet:
    before its drawn section, else at the end."""
    from tools.layout_draft import DRAWN_MARKER
    with open(path, encoding="utf-8") as f:
        lines = f.read().split("\n")
    if any(re.match(r"^  banks:\s*(#.*)?$", l) for l in lines):
        return
    marker = DRAWN_MARKER.split("{}")[0]
    at = next((i for i, l in enumerate(lines) if l.startswith(marker)), None)
    if at is None:
        at = len(lines)
        while at and not lines[at - 1].strip():
            at -= 1
    lines[at:at] = ["  banks:"]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _insert_banks(path, text_lines):
    """Append bank lines at the end of the board file's `banks:` block."""
    with open(path, encoding="utf-8") as f:
        lines = f.read().split("\n")
    start = next((i for i, l in enumerate(lines) if re.match(r"^  banks:\s*(#.*)?$", l)), None)
    if start is None:
        raise InventoryError("{}: no banks block".format(path))
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
    """product / summary in the board's file (inserted after its name, or
    replaced). Returns True when the file changed."""
    path = _board_path(board_id)
    with open(path, encoding="utf-8") as f:
        lines = f.read().split("\n")
    kept = [l for l in lines if not re.match(r"^  (product|summary):", l)]
    at = next((k for k, l in enumerate(kept) if re.match(r"^  name:", l)), None)
    if at is None:
        raise InventoryError("{}: no name line to put the product after".format(path))
    new = []
    if product_name:
        new.append("  product: {}".format(_flow(product_name)))
    if summary:
        new.append("  summary: {}".format(_flow(summary)))
    kept[at + 1:at + 1] = new
    if kept == lines:
        return False
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(kept))
    return True


# ---------------------------------------------------------------------------
# documents
# ---------------------------------------------------------------------------

def _record_documents(board_id, docs):
    """The inventory's documents into the board file's `documents:` (merged by
    URL; the block made after the catalogue fields when the board has none).
    Returns how many were added."""
    from tools.layout_draft import DRAWN_MARKER
    board = config_init.read_board(board_id)
    known = {d.get("url") for d in board.get("documents") or []}
    new = [d for d in docs or [] if d.get("url") and d["url"] not in known]
    if not new:
        return 0
    text = yaml.safe_dump(new, sort_keys=False, allow_unicode=True, width=120).rstrip("\n").split("\n")
    text = ["  " + l for l in text]
    path = board["_path"]
    with open(path, encoding="utf-8") as f:
        lines = f.read().split("\n")
    start = next((i for i, l in enumerate(lines) if re.match(r"^  documents:\s*$", l)), None)
    if start is not None:
        end = start + 1
        while end < len(lines) and (lines[end].startswith("  - ") or lines[end].startswith("    ") or not lines[end].strip()):
            end += 1
        while end > start + 1 and not lines[end - 1].strip():
            end -= 1
        lines[end:end] = text
    else:
        marker = DRAWN_MARKER.split("{}")[0]
        at = next((i for i, l in enumerate(lines) if re.match(r"^  (defaults|toolchain_options|banks|verification):", l)
                   or l.startswith(marker)), None)
        if at is None:
            at = len(lines)
            while at and not lines[at - 1].strip():
                at -= 1
        lines[at:at] = ["  documents:"] + text
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return len(new)


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------

def import_inventory(inv, write=True):
    """Import one inventory; the report of what it did (see plan())."""
    board_id = inv["board"]
    path = _board_path(board_id)
    existing = config_init.peek_board(board_id) or {}
    created = not existing.get("banks")                 # the board had no banks yet
    p = plan(inv, existing)
    banks = dict(existing.get("banks") or {})
    new = {b: (b, _balls(_as_bank_pins(d))) for b, d in p["add"]}
    everything = dict(banks, **new)
    text = []
    for b, d in p["add"]:
        text += _bank_text(b, d, _shares(b, new[b][1], everything))
    report = dict(p, board=board_id, file=os.path.relpath(path, os.path.dirname(config_init.dir_path)), created=created)
    if created and not p["add"]:
        raise InventoryError("board '{}': the inventory has no device with pins".format(board_id))
    if write:
        if text:
            _ensure_banks(path)
            _insert_banks(path, text)
        report["catalogue"] = _set_product(board_id, inv.get("product_name"), inv.get("summary"))
        config_init.clear_cache()
        report["documents"] = _record_documents(board_id, inv.get("documents"))
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

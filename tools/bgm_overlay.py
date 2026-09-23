#!/usr/bin/env python3
"""
tools/bgm_overlay.py -- the BGM parity overlay, config/bgm/<configuration>.yml.

A configuration under config/configurations/ describes hardware: which
peripheral sits on which pins, polarity, widths, clocks, I/O standards, the
constant ties an attached module needs. How basics-graphics-music's
board_specific_top.sv then *uses* that hardware for its labs is a set of
BGM conventions, not facts about the board: which key resets, that the
TM1638 is the lab's key / led / digit bus with the board LEDs on its low
bits, that the keys double as switches, that the lab runs on the pixel
clock, that LED bits are mirrored, that a header pin follows the reset.
Those live here, one file per configuration, and config/init.py applies
them on top of the generic configuration when a design follows BGM's
conventions (the imported designs do; `synthesize.py --no-bgm-overlay` or
UNIFPGA_BGM_OVERLAY=0 turns the overlay off and the generic composition,
buses concatenated in attach order, power-up reset, is generated instead).

    BGM:
      configuration: <id>
      variant: <boards/<variant> in BGM>
      reset:            { sources: [...], sync: 2 }  # codegen's reset vocabulary; sync = flops
                                                      # between the pin releasing and rst (c5gx);
                                                      # sync_assert: true delays the assertion instead
                                                      # (a7_lite's xpm_cdc_async_rst polarity slip)
      lab_clock:        pixel
      uart_rx:          0 | 1                         # what the lab reads when no UART pin is wired
      tie:              { <ref>: rst | ~rst | 0 | 1 } # pins BGM drives from its reset, or ties
                                                      # off instead of using the component
      lab_width:        { buttons: 8 }                # a lab bus wider than the bits wired to it
      attach:                                         # per attach, by peripheral and occurrence
        - { peripheral: tm1638_led_key, index: 0,
            lab_bits: {buttons: [0, 1, ...], ...},   # bits of the lab bus this attach carries
            params: {as_switches: true, mirror: true, direction: out} }
        - { peripheral: rgb_led, index: 0, drop: true }   # a component BGM ties off
        - { peripheral: seven_segment_per_digit, index: 0,
            bind: {dp: [onboard_leds[4], ...]},       # a signal BGM routes onto other pins
            params: {dp_active: low} }
        - { peripheral: lcd_480_272, index: 0, bind: {hs: null, vs: null} }   # signals BGM ties off
        - { peripheral: lcd_480_272, index: 0, params: {mirror_screen: true} }  # the lab gets
                                                      # screen_width - 1 - x, screen_height - 1 - y

tools/sync_from_bgm.py writes these files (--reset --clock-tree --polarity
--components --lab-bits); tools/equiv_check.py proves the result against
BGM's own top. `split` moves the fields out of configurations that still
carry them (the one-time migration).
"""

import argparse
import os
import re
import sys
from collections import OrderedDict

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OVERLAY_DIR = os.path.join(REPO, "config", "bgm")
CONFIG_DIR = os.path.join(REPO, "config", "configurations")

OVERLAY_PARAMS = ("as_switches", "mirror", "direction", "mirror_screen", "rst")   # attach params that are BGM conventions
REMOVE = object()                                          # set_attach(bind=...): delete an override
_TOP_KEYS = ("reset", "lab_clock", "uart_rx", "tie", "lab_width", "attach")
_RST_TIE = re.compile(r"^\s*~?\s*rst\s*$")


def path_for(configuration_id):
    return os.path.join(OVERLAY_DIR, configuration_id + ".yml")


def load(configuration_id):
    """The overlay dict (the `BGM:` mapping) or None."""
    p = path_for(configuration_id)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("BGM") or {}


def _header(configuration_id, variant):
    return ("# BGM parity overlay for {c}: how basics-graphics-music's boards/{v}/board_specific_top.sv\n"
            "# derives its reset and composes lab_top's buses on this board. BGM's lab conventions, not\n"
            "# facts about the hardware; config/configurations/{c}.yml stays generic and config/init.py\n"
            "# applies this on top of it (synthesize.py --no-bgm-overlay turns it off).\n"
            "# Written by tools/sync_from_bgm.py, proved by tools/equiv_check.py; do not edit by hand.\n"
            .format(c=configuration_id, v=variant or "?"))


def _ordered(data):
    """Stable key order for a readable diff."""
    out = OrderedDict()
    for key in ("configuration", "variant") + _TOP_KEYS:
        if key in data and data[key] not in (None, {}, []):
            out[key] = data[key]
    for key in data:
        if key not in out and data[key] not in (None, {}, []):
            out[key] = data[key]
    return out


class _Dumper(yaml.SafeDumper):
    pass


def _represent_ordered(dumper, data):
    return dumper.represent_mapping("tag:yaml.org,2002:map", data.items())


_Dumper.add_representer(OrderedDict, _represent_ordered)


def _flow_lists(dumper, data):
    # short lists of ints / None on one line (lab_bits), everything else block style
    if all(x is None or isinstance(x, (int, str)) for x in data) and len(data) <= 40:
        return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=True)
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=False)


_Dumper.add_representer(list, _flow_lists)


def save(configuration_id, data, variant=None):
    """Write the overlay; an empty overlay removes the file. Returns True when
    the file changed."""
    data = _ordered(dict(data or {}))
    data.pop("configuration", None)
    var = data.pop("variant", None) or variant
    p = path_for(configuration_id)
    if not any(k in data for k in _TOP_KEYS):
        if os.path.exists(p):
            os.remove(p)
            return True
        return False
    body = OrderedDict([("configuration", configuration_id), ("variant", var)])
    body.update(data)
    text = _header(configuration_id, var) + yaml.dump({"BGM": body}, Dumper=_Dumper, default_flow_style=False,
                                                       sort_keys=False, width=100, allow_unicode=True)
    os.makedirs(OVERLAY_DIR, exist_ok=True)
    old = open(p, encoding="utf-8").read() if os.path.exists(p) else None
    if old == text:
        return False
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return True


def update(configuration_id, variant=None, **fields):
    """Set top-level fields (reset=, lab_clock=, tie=, lab_width=); None removes one."""
    data = load(configuration_id) or {}
    if variant:
        data["variant"] = variant
    for key, value in fields.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    return save(configuration_id, data, variant)


def attach_override(data, peripheral, index, create=True):
    """The attach override entry for (peripheral, index) in an overlay dict."""
    entries = data.setdefault("attach", [])
    for e in entries:
        if e.get("peripheral") == peripheral and int(e.get("index", 0)) == int(index):
            return e
    if not create:
        return None
    e = OrderedDict([("peripheral", peripheral), ("index", int(index))])
    entries.append(e)
    return e


def set_attach(configuration_id, peripheral, index, lab_bits=None, params=None, variant=None, clear_lab_bits=False,
               drop=None, bind=None):
    """Set an attach's lab_bits, BGM params, bind additions and / or drop flag
    in the overlay. lab_bits None leaves it, {} / clear_lab_bits removes it;
    params and bind merge (a None value removes a key); drop True / False
    sets / clears the flag."""
    data = load(configuration_id) or {}
    e = attach_override(data, peripheral, index)
    if bind:
        cur = OrderedDict(e.get("bind") or {})
        for k, v in bind.items():
            if v is REMOVE:
                cur.pop(k, None)
            elif v is None:
                cur[k] = None                     # null: unbind the signal
            else:
                cur[k] = list(v) if isinstance(v, (list, tuple)) else v
        if cur:
            e["bind"] = cur
        else:
            e.pop("bind", None)
    if clear_lab_bits or lab_bits == {}:
        e.pop("lab_bits", None)
    elif lab_bits is not None:
        e["lab_bits"] = OrderedDict((k, list(v)) for k, v in lab_bits.items())
    if drop is True:
        e["drop"] = True
    elif drop is False:
        e.pop("drop", None)
    if params:
        cur = OrderedDict(e.get("params") or {})
        for k, v in params.items():
            if v is None:
                cur.pop(k, None)
            else:
                cur[k] = v
        if cur:
            e["params"] = cur
        else:
            e.pop("params", None)
    if len(e) <= 2:                                   # nothing left but the key
        data["attach"] = [x for x in data["attach"] if x is not e]
    return save(configuration_id, data, variant)


def enabled():
    return os.environ.get("UNIFPGA_BGM_OVERLAY", "1") not in ("0", "false", "no", "off")


# tools/sync_from_bgm.py --lab-bits plans over the configuration's attach
# list by position; while it resolves, dropped attaches stay in place (they
# provide no lab bus) so its indices are the configuration's
KEEP_DROPPED = False


def apply(cfg, attached, overlay):
    """Merge an overlay into a configuration dict (copied) and the resolved
    attach list (in place). Returns the new configuration dict."""
    if not overlay:
        return cfg
    cfg = dict(cfg)
    if overlay.get("reset") is not None:
        cfg["reset"] = overlay["reset"]
    if overlay.get("lab_clock") is not None:
        cfg["lab_clock"] = overlay["lab_clock"]
    if overlay.get("uart_rx") is not None:
        # BGM: `.uart_rx ( )` reads 0, `wire UART_RX = '1` reads 1; the generic
        # composition gives an unconnected receiver the idle line (1)
        cfg["uart_rx"] = int(overlay["uart_rx"])
    if overlay.get("tie"):
        tie = OrderedDict(cfg.get("tie") or {})
        tie.update(overlay["tie"])
        cfg["tie"] = tie
    if overlay.get("lab_width"):
        # the lab bus is wider than the bits wired to it (emooc_cc passes
        # w_key = 8 but wires 7 keys; the top bit reads 0)
        cfg["lab_width"] = {k: int(v) for k, v in overlay["lab_width"].items()}
    occ = {}
    dropped = []
    for a in attached:
        pid = a["peripheral_id"]
        i = occ.get(pid, 0)
        occ[pid] = i + 1
        for e in overlay.get("attach") or []:
            if e.get("peripheral") == pid and int(e.get("index", 0)) == i:
                if e.get("drop"):
                    # a component BGM's top does not use (Digilent RGB LEDs:
                    # every pin tied to a constant); its pins come from `tie`
                    if not KEEP_DROPPED:
                        dropped.append(a)
                    continue
                if e.get("lab_bits") is not None:
                    a["lab_bits"] = dict(e["lab_bits"])
                if e.get("params"):
                    a["params"] = dict(a.get("params") or {}, **e["params"])
                if e.get("bind"):
                    # a signal BGM routes somewhere the hardware description
                    # does not (the HEX decimal point onto the top LEDs); null
                    # unbinds an optional signal BGM ties off instead
                    # (tang_nano_20k's LCD_HS / LCD_VS)
                    b = dict(a.get("bind") or {})
                    for k, v in e["bind"].items():
                        if v is None:
                            b.pop(k, None)
                        else:
                            b[k] = v
                    a["bind"] = b
    for a in dropped:
        attached.remove(a)
    return cfg


# ---------------------------------------------------------------------------
# split: move the BGM fields out of the generic configurations (one-time)
# ---------------------------------------------------------------------------

def _strip_block(lines, key_re, indent):
    """Remove `<indent>key:` and its more-indented children (and a comment
    line directly above that mentions BGM / sync)."""
    i = 0
    removed = False
    while i < len(lines):
        if re.match(r"^" + indent + key_re + r":", lines[i]):
            j = i + 1
            while j < len(lines) and (lines[j].startswith(indent + " ") or lines[j].strip() == ""):
                if lines[j].strip() == "" and (j + 1 >= len(lines) or not lines[j + 1].startswith(indent + " ")):
                    break
                j += 1
            k = i
            if k > 0 and lines[k - 1].lstrip().startswith("#") and re.search(r"BGM|sync_from_bgm|lab_bits|Reset policy", lines[k - 1]):
                k -= 1
            del lines[k:j]
            removed = True
            continue
        i += 1
    return removed


def split_one(path, dry_run=False):
    text = open(path, encoding="utf-8").read()
    cfg = yaml.safe_load(text)["Configuration"]
    cid = cfg["id"]
    overlay = load(cid) or {}
    moved = []
    lines = text.split("\n")

    if cfg.get("reset") is not None:
        overlay["reset"] = cfg["reset"]
        _strip_block(lines, "reset", "  ")
        moved.append("reset")
    if cfg.get("lab_clock") is not None:
        overlay["lab_clock"] = cfg["lab_clock"]
        lines = [l for l in lines if not re.match(r"^  lab_clock:", l)]
        moved.append("lab_clock")
    ties = cfg.get("tie") or {}
    rst_ties = OrderedDict((k, v) for k, v in ties.items() if _RST_TIE.match(str(v)))
    if rst_ties:
        overlay["tie"] = OrderedDict(overlay.get("tie") or {})
        overlay["tie"].update(rst_ties)
        keep = [(k, v) for k, v in ties.items() if k not in rst_ties]
        # rewrite the tie block with the remaining entries
        i = next((n for n, l in enumerate(lines) if re.match(r"^  tie:", l)), None)
        if i is not None:
            j = i + 1
            while j < len(lines) and lines[j].startswith("    "):
                j += 1
            new = ["  tie:"] + ["    {}: {}".format('"{}"'.format(k) if "[" in str(k) else k, v) for k, v in keep] if keep else []
            k0 = i
            if k0 > 0 and lines[k0 - 1].lstrip().startswith("#") and "tie" in lines[k0 - 1].lower() and not keep:
                k0 -= 1
            lines[k0:j] = new
        moved.append("tie:" + ",".join(rst_ties))
    occ = {}
    for a in cfg.get("attach") or []:
        pid = a.get("peripheral")
        idx = occ.get(pid, 0)
        occ[pid] = idx + 1
        lb = a.get("lab_bits")
        params = {k: v for k, v in (a.get("params") or {}).items() if k in OVERLAY_PARAMS}
        if not lb and not params:
            continue
        e = attach_override(overlay, pid, idx)
        if lb:
            e["lab_bits"] = OrderedDict((k, list(v)) for k, v in lb.items())
        if params:
            e["params"] = OrderedDict(e.get("params") or {})
            e["params"].update(params)
        moved.append("{}#{}".format(pid, idx))
    if moved:
        # strip lab_bits sub-blocks and the overlay params from every attach
        out, i = [], 0
        while i < len(lines):
            l = lines[i]
            m = re.match(r"^(\s+)lab_bits:", l)
            if m:
                ind = m.group(1)
                i += 1
                while i < len(lines) and lines[i].startswith(ind + " "):
                    i += 1
                continue
            if re.match(r"^\s+(as_switches|mirror):", l):
                i += 1
                continue
            out.append(l)
            i += 1
        # a `params:` left without children
        lines, i = [], 0
        while i < len(out):
            l = out[i]
            if re.match(r"^\s+params:\s*$", l) and (i + 1 >= len(out) or not out[i + 1].startswith(re.match(r"^(\s+)", l).group(1) + " ")):
                i += 1
                continue
            lines.append(l)
            i += 1
    new_text = "\n".join(lines)
    while "\n\n\n" in new_text:
        new_text = new_text.replace("\n\n\n", "\n\n")
    if not moved:
        return "unchanged"
    yaml.safe_load(new_text)
    if not dry_run:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_text)
        from tools import bgm_oracle
        vdir = bgm_oracle.variant_dir_for(cid, cfg.get("board"))
        save(cid, overlay, os.path.basename(vdir) if vdir else None)
    return "moved " + ", ".join(moved)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("split", help="move reset / lab_clock / rst ties / lab_bits / as_switches / mirror out of the configurations")
    s.add_argument("--only", nargs="*")
    s.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    if args.cmd != "split":
        p.print_help()
        return 2
    sys.path.insert(0, REPO)
    for name in sorted(os.listdir(CONFIG_DIR)):
        if not name.endswith(".yml"):
            continue
        cid = name[:-4]
        if args.only and cid not in args.only:
            continue
        print("[split] {:44s} {}".format(cid, split_one(os.path.join(CONFIG_DIR, name), args.dry_run)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

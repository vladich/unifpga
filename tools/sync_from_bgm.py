#!/usr/bin/env python3
"""
Bring existing configurations in line with facts read from the BGM oracle
(tools/bgm_oracle.py). This is the incremental precursor of the
Verilog-driven generator planned in PLAN.md P2.4: it edits
config/configurations/<id>.yml in place, one fact class at a time, and is
idempotent.

    --reset      write the `reset:` policy derived from BGM's `wire rst = ...`
    --dry-run    print what would change, write nothing

The reset mapping (see codegen.reset_sources for the policy vocabulary):

    BGM expression                       ->  reset.sources
    rst_on_power_up                      ->  power_up: true
    sw [w_sw - 1] / SW [w_lab_sw]        ->  switch_msb: true
    | (~ KEY)                            ->  any_key: true
    ~ KEY [w_key - 1] / tm_key [msb]     ->  key: msb
    ~ KEY [0] / btn [0] / ~ BTN_N        ->  key: 0
    ~ RESET / ~ RST_N / ~ CPU_RESETN ... ->  pin (kept as the existing
                                              reset_button attach when present;
                                              otherwise a reset_button attach is
                                              added on the pinmap's reset bank)

Variants without a BGM directory are left untouched.
"""

import argparse
import glob
import os
import re
from collections import OrderedDict
import sys

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, REPO)

from config import init as config_init      # noqa: E402
from tools import bgm_oracle                # noqa: E402

_RESET_BANK_NAMES = ("cpu_resetn", "cpu_reset", "onboard_reset", "reset_n", "rst_n", "reset", "rst")


def _reset_bank(pinmap):
    banks = (pinmap or {}).get("pinBanks") or {}
    for name in _RESET_BANK_NAMES:
        if name in banks:
            return name
    for name in banks:
        low = name.lower()
        if ("reset" in low or low.endswith("rst") or low.endswith("rst_n")) and not low.startswith(("pcie", "ethernet", "qsfp", "audio", "arduino")):
            return name
    return None


def reset_policy_for(cfg, pinmap, facts):
    """Return (policy_dict_or_None, note). policy_dict is what goes under
    `reset:`; a `pin` kind is expressed through the reset_button attach and
    therefore returns a policy without a pin entry (plus a flag to add the
    attach when missing)."""
    kinds = set(facts["reset_kinds"])
    if not kinds:
        return None, "BGM derives no reset (kept default power_up)"
    sources = []
    if "power_up" in kinds:
        sources.append({"power_up": True})
    if "switch_msb" in kinds:
        sources.append({"switch_msb": True})
    if "any_key" in kinds:
        sources.append({"any_key": True})
    elif "key_msb" in kinds:
        sources.append({"key": "msb"})
    elif "key_0" in kinds:
        sources.append({"key": 0})
    need_pin = "pin" in kinds
    return {"sources": sources, "_need_pin": need_pin}, " | ".join(facts["reset_exprs"])


def _has_reset_button(cfg):
    return any((a or {}).get("peripheral") == "reset_button" for a in cfg.get("attach") or [])


def _render_reset_block(sources):
    lines = ["  # Reset policy derived from BGM's board_specific_top.sv (tools/sync_from_bgm.py --reset)",
             "  reset:", "    sources:"]
    for s in sources:
        (k, v), = s.items()
        lines.append("      - {}: {}".format(k, "true" if v is True else v))
    return "\n".join(lines) + "\n"


def _render_reset_button_attach(bank, active):
    return ("    - peripheral: reset_button   # BGM: dedicated reset pin (tools/sync_from_bgm.py --reset)\n"
            "      params:\n"
            "        active: {}\n"
            "      bind:\n"
            "        rst: {}\n").format(active, bank)


def apply_reset(path, dry_run):
    original = open(path, encoding="utf-8").read()
    text = original
    cfg = yaml.safe_load(text)["Configuration"]
    cid = cfg["id"]
    vdir = bgm_oracle.variant_dir_for(cid, cfg["board"])
    if vdir is None:
        return "skip (no BGM variant)"
    facts = bgm_oracle.summarize(vdir)
    policy, note = reset_policy_for(cfg, None, facts)
    if policy is None:
        return "skip ({})".format(note)
    pinmap = config_init.read_board_pinmap(cfg["board"]) or {}
    changes = []

    # 1. dedicated pin -> reset_button attach
    if policy.pop("_need_pin") and not _has_reset_button(cfg):
        bank = _reset_bank(pinmap)
        if bank is None:
            changes.append("WARNING: BGM uses a dedicated reset pin ({}) but the pinmap has no reset bank"
                           .format(note))
        else:
            active = "low" if re.search(r"~\s*\w*(RESET|RST|rstn|resetn|ck_rst)", note, re.I) or "rstn" in note else "high"
            attach_text = _render_reset_button_attach(bank, active)
            # insert as the first attach entry (after the `attach:` line)
            new_text, n = re.subn(r"^(  attach:\s*\n)", r"\1" + attach_text.replace("\\", "\\\\"), text, count=1, flags=re.M)
            if n == 1:
                text = new_text
                changes.append("add reset_button on {} (active {})".format(bank, active))
            else:
                changes.append("WARNING: could not find `attach:` to insert reset_button")

    # 2. reset: block (replace an existing one)
    sources = policy["sources"]
    existing = cfg.get("reset")
    if sources:
        block = _render_reset_block(sources)
        if existing is not None and block in text:
            sources = None          # already exactly what we would write
    if sources:
        if existing is not None:
            # remove the old block (our comment line, `  reset:` and its
            # indented children) so the sync stays idempotent
            text = re.sub(r"(?m)(?:^  # Reset policy derived[^\n]*\n)?^  reset:\n(?:^ {4,}.*\n)*\n?", "", text)
        # insert after the `toolchain:`/`part:` header lines
        m = re.search(r"^(  toolchain: .*\n(?:  part: .*\n)?)", text, re.M)
        if not m:
            changes.append("WARNING: no toolchain: line; reset block not inserted")
        else:
            text = text[:m.end()] + "\n" + block + text[m.end():]
            changes.append("reset.sources = {}".format(sources))
    elif existing is not None:
        changes.append("note: BGM uses only a dedicated pin; existing reset: block left as is")

    if not changes or text == original:
        return "unchanged"
    if not dry_run:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    return "; ".join(changes)


# ---------------------------------------------------------------------------
# --clock: board clock frequency into the pinmap, clock attach into configs
# ---------------------------------------------------------------------------

def _pinmap_path(board_id):
    entry = config_init.read_boards_catalog().get(board_id)
    if entry is None:
        return None
    return os.path.join(REPO, "config", "boards", entry["_producer_dir"], entry["_family_dir"], board_id + ".yml")


def _clock_banks(pinmap):
    banks = (pinmap or {}).get("pinBanks") or {}
    return [k for k in banks if any(t in k.lower() for t in ("clk", "clock", "osc"))]


def _clock_attach(cfg):
    for a in cfg.get("attach") or []:
        if (a or {}).get("peripheral") == "clock_input":
            return a
    return None


def _set_bank_frequency(pinmap_text, bank, mhz):
    """Insert `frequency_mhz: <mhz>` into the bank's YAML (inline or block form)."""
    val = int(mhz) if float(mhz).is_integer() else mhz
    inline = re.compile(r"^(    {}:\s*\{{\s*pins:\s*[^}}]*?)(\s*\}})".format(re.escape(bank)), re.M)
    if inline.search(pinmap_text):
        return inline.sub(lambda m: "{}, frequency_mhz: {}{}".format(m.group(1), val, m.group(2)), pinmap_text, count=1)
    block = re.compile(r"^(    {}:\s*\n)".format(re.escape(bank)), re.M)
    if block.search(pinmap_text):
        return block.sub(lambda m: "{}      frequency_mhz: {}\n".format(m.group(1), val), pinmap_text, count=1)
    return None


def apply_clock(paths, dry_run):
    """Two things: (1) every board's system clock bank gets `frequency_mhz`
    from BGM's `clk_mhz` (pinmap is the single source, resolve_clock reads it);
    (2) a configuration without a clock_input attach, or whose clock bind
    points at a bank that does not exist, is pointed at the pinmap's clock bank."""
    results = {}
    board_mhz = {}      # board -> {mhz: [config ids]}
    cfgs = {}
    for path in paths:
        cid = os.path.splitext(os.path.basename(path))[0]
        cfg = yaml.safe_load(open(path, encoding="utf-8"))["Configuration"]
        cfgs[cid] = (path, cfg)
        vdir = bgm_oracle.variant_dir_for(cid, cfg["board"])
        mhz = bgm_oracle.summarize(vdir)["clk_mhz"] if vdir else None
        if mhz is not None:
            board_mhz.setdefault(cfg["board"], {}).setdefault(mhz, []).append(cid)

    # (1) pinmaps
    for board, by_mhz in sorted(board_mhz.items()):
        if len(by_mhz) > 1:
            results[board] = "CONFLICT: BGM clk_mhz differs between variants: {}".format(
                {k: v for k, v in by_mhz.items()})
            continue
        (mhz, _cids), = by_mhz.items()
        pm_path = _pinmap_path(board)
        pinmap = config_init.read_board_pinmap(board) or {}
        banks = (pinmap.get("pinBanks") or {})
        clocks = _clock_banks(pinmap)
        if not pm_path or not clocks:
            results[board] = "WARNING: no clock bank in pinmap"
            continue
        # The system clock: the bank the configs bind, else the first clock bank.
        bound = None
        for cid in _cids:
            a = _clock_attach(cfgs[cid][1])
            if a and (a.get("bind") or {}).get("clk") in banks:
                bound = a["bind"]["clk"]
                break
        bank = bound or clocks[0]
        have = (banks.get(bank) or {}).get("frequency_mhz")
        if have is not None:
            if abs(float(have) - float(mhz)) > 0.01:
                results[board] = "CONFLICT: pinmap {} says {} MHz, BGM says {}".format(bank, have, mhz)
            else:
                results[board] = "pinmap {} already {} MHz".format(bank, have)
            continue
        text = open(pm_path, encoding="utf-8").read()
        new = _set_bank_frequency(text, bank, mhz)
        if new is None:
            results[board] = "WARNING: could not edit bank {} in {}".format(bank, pm_path)
            continue
        if not dry_run:
            open(pm_path, "w", encoding="utf-8").write(new)
            config_init.clear_cache()
        results[board] = "pinmap {}: frequency_mhz = {} (BGM)".format(bank, mhz)

    # (2) configurations
    for cid, (path, cfg) in sorted(cfgs.items()):
        pinmap = config_init.read_board_pinmap(cfg["board"]) or {}
        banks = (pinmap.get("pinBanks") or {})
        clocks = [b for b in _clock_banks(pinmap) if (banks.get(b) or {}).get("frequency_mhz") is not None] \
            or _clock_banks(pinmap)
        a = _clock_attach(cfg)
        text = open(path, encoding="utf-8").read()
        if a is None:
            if not clocks:
                results[cid] = "WARNING: no clock_input and no clock bank"
                continue
            block = ("    - peripheral: clock_input   # added by tools/sync_from_bgm.py --clock\n"
                     "      bind:\n        clk: {}\n").format(clocks[0])
            new, n = re.subn(r"^(  attach:\s*\n)", r"\1" + block, text, count=1, flags=re.M)
            results[cid] = "add clock_input on {}".format(clocks[0]) if n else "WARNING: no attach: line"
        else:
            bound = (a.get("bind") or {}).get("clk")
            if bound in banks:
                results.setdefault(cid, "ok")
                continue
            if not clocks:
                results[cid] = "WARNING: clock bind {!r} missing and no clock bank".format(bound)
                continue
            new = re.sub(r"(peripheral: clock_input\n(?:.*\n)*?\s*clk:\s*){}\b".format(re.escape(str(bound))),
                         r"\g<1>" + clocks[0], text, count=1)
            results[cid] = "clock bind {} -> {}".format(bound, clocks[0]) if new != text else "WARNING: could not rewrite clock bind"
        if new != text and not dry_run:
            open(path, "w", encoding="utf-8").write(new)
    return results


# ---------------------------------------------------------------------------
# --seven-seg: bind the on-board 7-segment display by the pinmap's shape
# ---------------------------------------------------------------------------

def _bgm_seven_seg_by_pin(vdir):
    """{normalized pin: (kind, bit, inverted)} from BGM's assigns (E5)."""
    text = bgm_oracle.preprocess_variant(vdir).text
    sig_pins = _bgm_signal_pins(vdir)
    m = bgm_oracle.expand_seven_seg_map(bgm_oracle.seven_seg_map(text), sig_pins)
    return {sig_pins[k]: v for k, v in m.items() if k in sig_pins}


def _seven_seg_attach(pinmap, bgm_by_pin=None):
    """Render the attach block for onboard_7seg according to the pinmap shape,
    or None when the board has no onboard_7seg bank. With `bgm_by_pin` (BGM's
    seven_seg_map by physical pin) the segment / dp / digit binds follow BGM's
    bit order and the two polarities come from its inversions."""
    bank = ((pinmap or {}).get("pinBanks") or {}).get("onboard_7seg")
    pins = (bank or {}).get("pins")
    if not isinstance(pins, dict):
        return None
    if bgm_by_pin:
        block = _seven_seg_attach_from_bgm(pins, bgm_by_pin)
        if block is not None:
            return block
    hex_keys = sorted((k for k in pins if re.match(r"^hex\d+$", k)), key=lambda k: int(k[3:]))
    if hex_keys:
        segs = {len(pins[k]) for k in hex_keys}
        if len(segs) != 1:
            return None
        (n_segs,) = segs
        refs = ", ".join("onboard_7seg.{}".format(k) for k in hex_keys)
        return ("    - peripheral: seven_segment_per_digit\n"
                "      params:\n"
                "        digits: {d}\n"
                "        segs: {s}\n"
                "      bind:\n"
                "        hex: [{refs}]\n").format(d=len(hex_keys), s=n_segs, refs=refs)
    anodes = pins.get("anodes")
    if not isinstance(anodes, list):
        return None
    lines = ["    - peripheral: seven_segment_8digit_shared",
             "      params:",
             "        digits: {}".format(len(anodes)),
             "      bind:"]
    if isinstance(pins.get("segments"), list):
        n = len(pins["segments"])
        if n == 7:
            lines.append("        segments: onboard_7seg.segments")
            if "dp" in pins:
                lines.append("        dp: onboard_7seg.dp")
        elif n == 8:
            lines.append("        segments: [{}]".format(", ".join(
                '"onboard_7seg.segments[{}]"'.format(i) for i in range(7))))
            lines.append('        dp: "onboard_7seg.segments[7]"')
        else:
            return None
    elif all(k in pins for k in ("ca", "cb", "cc", "cd", "ce", "cf", "cg")):
        lines.append("        segments: [{}]".format(", ".join(
            "onboard_7seg.c{}".format(c) for c in "abcdefg")))
        if "dp" in pins:
            lines.append("        dp: onboard_7seg.dp")
    else:
        return None
    lines.append("        digits: onboard_7seg.anodes")
    return "\n".join(lines) + "\n"


# Generated configurations indent list items by 4, hand-written twins by 2.
_ATTACH_START = re.compile(r"^( {2,4})- peripheral: (seven_segment_8digit_shared|seven_segment_per_digit)\s*$")


def _reindent(block, item_indent):
    """The rendered blocks use the generated layout (item at 4 spaces, keys at
    6, values at 8); shift them for a file whose items sit at `item_indent`."""
    delta = len(item_indent) - 4
    if delta == 0:
        return block
    out = []
    for line in block.split("\n"):
        stripped = line.lstrip(" ")
        cur = len(line) - len(stripped)
        out.append(" " * max(0, cur + delta) + stripped if stripped else line)
    return "\n".join(out)


def _seven_seg_attach_from_bgm(pins, bgm_by_pin):
    """Binds for a shared display from BGM's per-pin map: segments a..g =
    the pins BGM drives from abcdefgh[7..1], dp = abcdefgh[0], digits = the
    pins BGM drives from digit[0..n-1]; polarity params from the inversions.
    None when BGM's map does not cover the bank's pins."""
    if any(re.match(r"^hex\d+$", k) for k in pins):
        return None
    refs = {}                                   # normalized pin -> bind ref
    for sub, val in pins.items():
        if isinstance(val, list):
            for i, p in enumerate(val):
                if p is not None:
                    refs[_norm_pin(p)] = "onboard_7seg.{}[{}]".format(sub, i)
        elif isinstance(val, str):
            refs[_norm_pin(val)] = "onboard_7seg.{}".format(sub)
    seg_pins = {bit: p for p, (kind, bit, _inv) in bgm_by_pin.items() if kind == "seg" and p in refs}
    dig_pins = {bit: p for p, (kind, bit, _inv) in bgm_by_pin.items() if kind == "dig" and p in refs}
    if not all(b in seg_pins for b in range(1, 8)) or not dig_pins:
        return None
    n_dig = max(dig_pins) + 1
    if sorted(dig_pins) != list(range(n_dig)):
        return None
    seg_inv = {bgm_by_pin[p][2] for p in seg_pins.values()}
    dig_inv = {bgm_by_pin[p][2] for p in dig_pins.values()}
    if len(seg_inv) != 1 or len(dig_inv) != 1:
        return None
    lines = ["    - peripheral: seven_segment_8digit_shared   # bit order and polarity from BGM's assigns (sync --seven-seg)",
             "      params:",
             "        digits: {}".format(n_dig),
             "        active: {}".format("low" if seg_inv.pop() else "high"),
             "        digits_active: {}".format("low" if dig_inv.pop() else "high"),
             "      bind:",
             "        segments: [{}]".format(", ".join('"{}"'.format(refs[seg_pins[b]]) for b in range(7, 0, -1)))]
    if 0 in seg_pins:
        lines.append('        dp: "{}"'.format(refs[seg_pins[0]]))
    lines.append("        digits: [{}]".format(", ".join('"{}"'.format(refs[dig_pins[b]]) for b in range(n_dig))))
    return "\n".join(lines) + "\n"


def apply_seven_seg(path, dry_run):
    original = open(path, encoding="utf-8").read()
    cfg = yaml.safe_load(original)["Configuration"]
    pinmap = config_init.read_board_pinmap(cfg["board"]) or {}
    config_init._apply_pin_overrides(cfg["id"], cfg, pinmap)
    vdir = bgm_oracle.variant_dir_for(cfg["id"], cfg["board"])
    bgm_by_pin = _bgm_seven_seg_by_pin(vdir) if vdir else None
    block = _seven_seg_attach(pinmap, bgm_by_pin)
    lines = original.split("\n")
    starts = [i for i, l in enumerate(lines) if _ATTACH_START.match(l)]
    if not starts:
        return "skip (no 7-segment attach)"
    if block is None:
        return "WARNING: pinmap onboard_7seg shape not understood"
    i = starts[0]
    item_indent = _ATTACH_START.match(lines[i]).group(1)
    key_indent = item_indent + "  "
    j = i + 1
    while j < len(lines) and (lines[j].startswith(key_indent) or lines[j].strip() == "") \
            and not lines[j].startswith(item_indent + "- "):
        # stop at a blank line followed by a comment/section above key level
        if lines[j].strip() == "" and j + 1 < len(lines) and not lines[j + 1].startswith(key_indent):
            break
        j += 1
    new_lines = lines[:i] + _reindent(block, item_indent).rstrip("\n").split("\n") + lines[j:]
    text = "\n".join(new_lines)
    if not text.endswith("\n"):
        text += "\n"
    if text == original:
        return "unchanged"
    if not dry_run:
        open(path, "w", encoding="utf-8").write(text)
    return "rewrote 7-segment attach: " + block.split("\n")[0].strip()


# ---------------------------------------------------------------------------
# --prune-optional: drop binds of optional signals whose pins do not exist
# ---------------------------------------------------------------------------

def apply_prune_optional(path, dry_run):
    from tools import codegen
    original = open(path, encoding="utf-8").read()
    cfg = yaml.safe_load(original)["Configuration"]
    pinmap = config_init.read_board_pinmap(cfg["board"]) or {}
    config_init._apply_pin_overrides(cfg["id"], cfg, pinmap)
    peripherals = config_init.read_peripherals()
    lines = original.split("\n")
    drop = []          # (peripheral id, signal, ref)
    for a in cfg.get("attach") or []:
        perif = peripherals.get(a.get("peripheral")) or {}
        sig_defs = {s["name"]: s for s in perif.get("signals", [])}
        for sig, ref in (a.get("bind") or {}).items():
            sdef = sig_defs.get(sig)
            if not sdef or not sdef.get("optional") or not isinstance(ref, str):
                continue
            parsed = codegen._parse_bank_ref(ref)
            if parsed is None:
                continue
            if codegen._bank_pin(pinmap, *parsed) is None:
                drop.append((a.get("peripheral"), sig, ref))
    if not drop:
        return "unchanged"
    new_lines = []
    for line in lines:
        m = re.match(r"^\s{6,8}([A-Za-z_][A-Za-z0-9_]*):\s*([^#\n]+?)\s*(#.*)?$", line)
        if m and any(m.group(1) == sig and m.group(2).strip().strip('"\'') == ref for _p, sig, ref in drop):
            continue
        new_lines.append(line)
    text = "\n".join(new_lines)
    if text == original:
        return "unchanged"
    if not dry_run:
        open(path, "w", encoding="utf-8").write(text)
    return "dropped optional binds without pins: " + ", ".join("{}.{}".format(p, s) for p, s, _ in drop)


# ---------------------------------------------------------------------------
# --vga: colour widths from the pinmap (pins) and BGM (user-visible bits)
# ---------------------------------------------------------------------------

_W_RE = re.compile(r"^\s*(?:localparam|parameter)?\s*(w_red|w_green|w_blue)\s*=\s*(\d+)", re.M)


def _bgm_colour_bits(vdir):
    if vdir is None:
        return {}
    text = bgm_oracle.preprocess_variant(vdir).text
    out = {}
    for m in _W_RE.finditer(text):
        out.setdefault(m.group(1), int(m.group(2)))
    return out


def apply_vga(path, dry_run):
    original = open(path, encoding="utf-8").read()
    cfg = yaml.safe_load(original)["Configuration"]
    if not any((a or {}).get("peripheral") == "vga_4bit" for a in cfg.get("attach") or []):
        return "skip (no vga_4bit)"
    pinmap = config_init.read_board_pinmap(cfg["board"]) or {}
    vga = ((pinmap.get("pinBanks") or {}).get("onboard_vga") or {}).get("pins")
    if not isinstance(vga, dict):
        return "WARNING: pinmap has no onboard_vga dict bank"
    vdir = bgm_oracle.variant_dir_for(cfg["id"], cfg["board"])
    bits = _bgm_colour_bits(vdir)
    user = {c: bits.get("w_" + n, 4) for c, n in (("r", "red"), ("g", "green"), ("b", "blue"))}
    if isinstance(vga.get("rgb"), list) and len(vga["rgb"]) == 3:
        # BGM: VGA_RGB = display_on ? {|red, |green, |blue} : 0  -> [2]=r [1]=g [0]=b
        pins = {"r": 1, "g": 1, "b": 1}
        binds = {"r": '["onboard_vga.rgb[2]"]', "g": '["onboard_vga.rgb[1]"]', "b": '["onboard_vga.rgb[0]"]'}
    else:
        pins, binds = {}, {}
        for c in "rgb":
            v = vga.get(c)
            if isinstance(v, list):
                pins[c] = len(v)
            elif isinstance(v, str):
                pins[c] = 1
            else:
                return "WARNING: onboard_vga has no {} sub-key".format(c)
            binds[c] = "onboard_vga." + c
    hs = "onboard_vga.hs" if "hs" in vga else None
    vs = "onboard_vga.vs" if "vs" in vga else None
    if not hs or not vs:
        return "WARNING: onboard_vga lacks hs/vs"
    depth = {(4, 4, 4): 444, (8, 8, 8): 888, (5, 6, 5): 565, (1, 1, 1): 111}.get((user["r"], user["g"], user["b"]), 444)
    block = ["    - peripheral: vga_4bit   # widths: pins from the pinmap, bits from BGM w_red/w_green/w_blue",
             "      params:",
             "        bits_r: {}".format(user["r"]), "        bits_g: {}".format(user["g"]), "        bits_b: {}".format(user["b"]),
             "        pin_bits_r: {}".format(pins["r"]), "        pin_bits_g: {}".format(pins["g"]), "        pin_bits_b: {}".format(pins["b"]),
             "        color_depth: {}".format(depth),
             "      bind:",
             "        r: {}".format(binds["r"]), "        g: {}".format(binds["g"]), "        b: {}".format(binds["b"]),
             "        hs: {}".format(hs), "        vs: {}".format(vs)]
    lines = original.split("\n")
    start = re.compile(r"^( {2,4})- peripheral: vga_4bit\s*(#.*)?$")
    starts = [i for i, l in enumerate(lines) if start.match(l)]
    if not starts:
        return "WARNING: could not find the vga_4bit attach line"
    i = starts[0]
    item_indent = start.match(lines[i]).group(1)
    key_indent = item_indent + "  "
    j = i + 1
    while j < len(lines) and (lines[j].startswith(key_indent) or lines[j].strip() == "") \
            and not lines[j].startswith(item_indent + "- "):
        if lines[j].strip() == "" and j + 1 < len(lines) and not lines[j + 1].startswith(key_indent):
            break
        j += 1
    new_lines = lines[:i] + _reindent("\n".join(block), item_indent).split("\n") + lines[j:]
    text = "\n".join(new_lines)
    if not text.endswith("\n"):
        text += "\n"
    if text == original:
        return "unchanged"
    if not dry_run:
        open(path, "w", encoding="utf-8").write(text)
    return "vga: bits {r}/{g}/{b}, pins {pr}/{pg}/{pb}{rgb}".format(
        r=user["r"], g=user["g"], b=user["b"], pr=pins["r"], pg=pins["g"], pb=pins["b"],
        rgb=" (rgb bank)" if "rgb" in vga else "")


# ---------------------------------------------------------------------------
# --sv-binds: driver peripherals bound exactly as BGM instantiates them
# ---------------------------------------------------------------------------

# BGM module -> (peripheral id, {module port: peripheral signal})
_SV_MODULES = {
    "tm1638_board_controller":        ("tm1638_led_key",  {"sio_clk": "clk", "sio_stb": "stb", "sio_data": "dio"}),
    "inmp441_mic_i2s_receiver":       ("inmp441_i2s_mic", {"lr": "lr", "ws": "ws", "sck": "sck", "sd": "sd"}),
    "inmp441_mic_i2s_receiver_alt":   ("inmp441_i2s_mic", {"lr": "lr", "ws": "ws", "sck": "sck", "sd": "sd"}),
    "digilent_pmod_mic3_spi_receiver": ("pmod_mic3",      {"cs": "cs", "sck": "sclk", "sdo": "miso"}),
}
_SV_PERIPHERALS = {pid for pid, _ in _SV_MODULES.values()} | {"hdmi_tmds"}

_EXPR = re.compile(r"^~?\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\[\s*(\d+)\s*\])?$")


def _norm_pin(p):
    return str(p).strip().strip('"').upper().replace("PIN_", "")


def _bgm_signal_pins(vdir):
    """{SIGNAL or SIGNAL[idx] (upper-cased): pin} from every constraint file of
    the variant, primary file first (later files never override)."""
    from tools import import_constraints as ic
    out = {}
    for path in ic.find_all_constraint_files(vdir):
        try:
            signals, _fmt = ic.parse_file(path)
        except Exception:
            continue
        for name, entry in signals.items():
            key = name.replace(" ", "").upper()
            out.setdefault(key, _norm_pin(entry["pin"]))
    return out


def _pin_to_ref(pinmap):
    """Reverse map: normalized pin -> bank ref usable in a bind. A "P,N" pair
    entry maps both the pair string and its P pin to the P ref (BGM
    constrains TMDS either way: `69,68` under Gowin EDA, `69` under yosys)."""
    out = {}

    def put(p, ref):
        out.setdefault(_norm_pin(p), ref)
        if "," in str(p):
            out.setdefault(_norm_pin(str(p).split(",", 1)[0]), ref)

    for bank, b in (pinmap.get("pinBanks") or {}).items():
        pins = (b or {}).get("pins")
        if isinstance(pins, str):
            put(pins, bank)
        elif isinstance(pins, list):
            for i, p in enumerate(pins):
                if p is not None:
                    put(p, "{}[{}]".format(bank, i))
        elif isinstance(pins, dict):
            for sub, v in pins.items():
                if isinstance(v, list):
                    for i, p in enumerate(v):
                        if p is not None:
                            put(p, "{}.{}[{}]".format(bank, sub, i))
                elif isinstance(v, str):
                    put(v, "{}.{}".format(bank, sub))
    return out


_TMDS_MODULES = ("DVI_TX_Top", "dvi_top", "HDMI", "hdmi", "TMDS_encoder", "hdmi_tmds_out")
# transmitter ports carrying the pairs when the constraint names do not say TMDS
# (colorlight75b: `hdmi i_hdmi (.TMDSp (HDMI_P[2:0]), .TMDSp_clock (HDMI_P[3]), ...)`)
_TMDS_PORTS = {"TMDSp": ("d_p", True), "TMDSn": ("d_n", True), "TMDSp_clock": ("clk_p", False), "TMDSn_clock": ("clk_n", False),
               "O_TMDS_DATA_P": ("d_p", True), "O_TMDS_DATA_N": ("d_n", True), "O_TMDS_CLK_P": ("clk_p", False), "O_TMDS_CLK_N": ("clk_n", False)}
_TMDS_PATTERNS = (
    # TMDS_CLK_P, O_TMDS_CLK_N, TMDS_0_CLK_P
    (re.compile(r"^(?:O_)?TMDS(?:_\d)?_(?:CLK|CLOCK)_([PN])$"), False),
    # TMDS_D_P[0], O_TMDS_DATA_N[2], TMDS_0_D_P[1]
    (re.compile(r"^(?:O_)?TMDS(?:_\d)?_(?:D|DATA)_([PN])\[(\d+)\]$"), True),
    # TMDSp_clock / TMDSn_clock (tang_nano_9k_hdmi_no_ip_tm1638)
    (re.compile(r"^TMDS([PN])_CLOCK$"), False),
    # TMDSp[0] / TMDSn[2]
    (re.compile(r"^TMDS([PN])\[(\d+)\]$"), True),
)


def _tmds_signal(key):
    """(polarity 'p'/'n', index or None) for a BGM TMDS signal name, else None."""
    for rx, indexed in _TMDS_PATTERNS:
        m = rx.match(key)
        if m:
            return m.group(1).lower(), (int(m.group(2)) if indexed else None)
    return None


def _bgm_tmds_binds(text, sig_pins, rev, pinmap):
    """hdmi_tmds binds when BGM's active text instantiates a TMDS transmitter:
    every TMDS_CLK_P / TMDS_D_P[i] (and _N) signal of the constraint files
    mapped to our pinmap by pin identity. Returns (binds, notes) or None."""
    if not any(bgm_oracle.instantiations(text, m) for m in _TMDS_MODULES):
        return None
    # The Tang Nano 9K shares its TMDS pins with LARGE_LCD colour pins; a TMDS
    # signal resolves to the HDMI bank when one claims the pin.
    hdmi_banks = {b: v for b, v in (pinmap.get("pinBanks") or {}).items()
                  if re.search(r"hdmi|tmds|dvi", b, re.I)}
    rev_hdmi = _pin_to_ref({"pinBanks": hdmi_banks}) if hdmi_banks else {}
    found, notes = {}, []
    for key, pin in sig_pins.items():
        parsed = _tmds_signal(key)
        if parsed is None:
            continue
        pol, idx = parsed
        sig = ("clk_" if idx is None else "d_") + pol
        ref = rev_hdmi.get(pin) or rev.get(pin)
        if ref is None:
            notes.append("{}: pin {} is not in the pinmap".format(key, pin))
            continue
        if idx is None:
            found[sig] = ref
        else:
            found.setdefault(sig, {})[idx] = ref
    if not found:
        # the pairs travel under other names: follow the transmitter's ports
        for module in _TMDS_MODULES:
            for inst in bgm_oracle.instantiations(text, module):
                for port, expr in inst["ports"]:
                    if port not in _TMDS_PORTS or not expr.strip():
                        continue
                    sig, indexed = _TMDS_PORTS[port]
                    m = re.match(r"^([A-Za-z_]\w*)\s*(?:\[\s*(\d+)\s*(?::\s*(\d+))?\s*\])?$", expr.strip())
                    if not m:
                        notes.append("{}: expression {!r} not a port".format(port, expr))
                        continue
                    name, hi, lo = m.group(1).upper(), m.group(2), m.group(3)
                    if indexed:
                        if hi is None:
                            keys = sorted((k for k in sig_pins if re.match(re.escape(name) + r"\[\d+\]$", k)),
                                          key=lambda k: int(k[len(name) + 1:-1]))
                        else:
                            lo_i = int(lo) if lo is not None else int(hi)
                            keys = ["{}[{}]".format(name, i) for i in range(lo_i, int(hi) + 1)]
                        for i, key in enumerate(keys):
                            ref = rev_hdmi.get(sig_pins.get(key)) or rev.get(sig_pins.get(key))
                            if ref is None:
                                notes.append("{}: {} is not in the pinmap".format(port, key))
                                continue
                            found.setdefault(sig, {})[i] = ref
                    else:
                        key = name if hi is None else "{}[{}]".format(name, hi)
                        ref = rev_hdmi.get(sig_pins.get(key)) or rev.get(sig_pins.get(key))
                        if ref is None:
                            notes.append("{}: {} is not in the pinmap".format(port, key))
                            continue
                        found[sig] = ref
    binds = {}
    for sig, val in found.items():
        if isinstance(val, dict):
            n = max(val) + 1
            if sorted(val) != list(range(n)):
                notes.append("{}: BGM constrains bits {} only".format(sig, sorted(val)))
                continue
            refs = [val[i] for i in range(n)]
            # collapse `bank.sub[0..n-1]` into the whole sub-bank
            m0 = re.match(r"^(.*)\[0\]$", refs[0])
            if m0 and all(r == "{}[{}]".format(m0.group(1), i) for i, r in enumerate(refs)):
                binds[sig] = m0.group(1)
            else:
                binds[sig] = refs
        else:
            binds[sig] = val
    # N halves BGM leaves to the tool (pair syntax): take them from our pinmap
    for p_sig, n_sig in (("clk_p", "clk_n"), ("d_p", "d_n")):
        if p_sig in binds and n_sig not in binds and isinstance(binds[p_sig], str):
            bank_sub = binds[p_sig].rsplit(".", 1)
            if len(bank_sub) == 2:
                bank, sub = bank_sub
                n_sub = sub[:-2] + "_n" if sub.endswith("_p") else None
                pins = ((pinmap.get("pinBanks") or {}).get(bank) or {}).get("pins") or {}
                if n_sub and isinstance(pins, dict) and n_sub in pins:
                    binds[n_sig] = "{}.{}".format(bank, n_sub)
    return binds, notes


def _bgm_driver_binds(vdir, pinmap):
    """{peripheral id: ({signal: bank ref}, notes)} for every _SV_MODULES module
    BGM instantiates in this variant's active text, plus hdmi_tmds when BGM
    instantiates a TMDS transmitter (binds by pin identity)."""
    text = bgm_oracle.preprocess_variant(vdir).text
    sig_pins = _bgm_signal_pins(vdir)
    rev = _pin_to_ref(pinmap)
    out = {}
    tmds = _bgm_tmds_binds(text, sig_pins, rev, pinmap)
    if tmds is not None:
        out["hdmi_tmds"] = tmds
    for module, (pid, port_map) in _SV_MODULES.items():
        insts = bgm_oracle.instantiations(text, module)
        if not insts or pid in out:
            continue
        inst = insts[0]
        binds, notes = {}, []
        for port, expr in inst["ports"]:
            sig = port_map.get(port)
            if sig is None:
                continue
            m = _EXPR.match(expr.strip())
            if not m:
                notes.append("{}: expression {!r} not a port".format(port, expr))
                continue
            name, idx = m.group(1).upper(), m.group(2)
            key = "{}[{}]".format(name, idx) if idx is not None else name
            pin = sig_pins.get(key)
            if pin is None:
                notes.append("{}: BGM signal {} has no pin in the constraint files".format(port, key))
                continue
            ref = rev.get(pin)
            if ref is None:
                notes.append("{}: pin {} ({}) is not in the pinmap".format(port, pin, key))
                continue
            binds[sig] = ref
        out[pid] = (binds, notes)
    return out


def _pins_of_ref(pinmap, ref):
    """Normalized physical pins behind a bind ref (`bank`, `bank.sub`,
    `bank[i]`, `bank.sub[i]`), both halves of a "P,N" pair."""
    m = re.match(r"^([A-Za-z_][\w]*)(?:\.([\w]+))?(?:\[(\d+)\])?$", str(ref).strip().strip('"'))
    if not m:
        return set()
    bank, sub, idx = m.group(1), m.group(2), m.group(3)
    pins = ((pinmap.get("pinBanks") or {}).get(bank) or {}).get("pins")
    if isinstance(pins, dict):
        pins = pins.get(sub) if sub else None
    if pins is None:
        return set()
    vals = pins if isinstance(pins, list) else [pins]
    if idx is not None:
        i = int(idx)
        vals = [vals[i]] if i < len(vals) else []
    out = set()
    for v in vals:
        if v is None:
            continue
        for half in str(v).split(","):
            out.add(_norm_pin(half))
    return out


def _find_attach_blocks(lines, pid):
    """[(start, end)] line ranges of `- peripheral: <pid>` items."""
    start = re.compile(r"^( {2,4})- peripheral: " + re.escape(pid) + r"\s*(#.*)?$")
    blocks = []
    for i, l in enumerate(lines):
        m = start.match(l)
        if not m:
            continue
        item_indent = m.group(1)
        key_indent = item_indent + "  "
        j = i + 1
        while j < len(lines) and (lines[j].startswith(key_indent) or lines[j].strip() == "") \
                and not lines[j].startswith(item_indent + "- "):
            if lines[j].strip() == "" and j + 1 < len(lines) and not lines[j + 1].startswith(key_indent):
                break
            j += 1
        blocks.append((i, j, item_indent))
    return blocks


def _attach_item_indent(lines):
    for l in lines:
        m = re.match(r"^( {2,4})- peripheral:", l)
        if m:
            return m.group(1)
    return "    "


def _render_attach(pid, params, binds, comment, indent):
    out = ["{}- peripheral: {}   # {}".format(indent, pid, comment)]
    if params:
        out.append(indent + "  params:")
        for k, v in params.items():
            out.append("{}    {}: {}".format(indent, k, v))
    out.append(indent + "  bind:")
    for k, v in binds.items():
        out.append('{}    {}: "{}"'.format(indent, k, v) if any(c in v for c in "[],") else "{}    {}: {}".format(indent, k, v))
    return out


def apply_sv_binds(path, dry_run):
    from tools import codegen
    original = open(path, encoding="utf-8").read()
    cfg = yaml.safe_load(original)["Configuration"]
    vdir = bgm_oracle.variant_dir_for(cfg["id"], cfg["board"])
    if vdir is None:
        return "skip (no BGM variant)"
    pinmap = config_init.read_board_pinmap(cfg["board"]) or {}
    config_init._apply_pin_overrides(cfg["id"], cfg, pinmap)
    derived = _bgm_driver_binds(vdir, pinmap)
    lines = original.split("\n")
    item_indent = _attach_item_indent(lines)
    changes, warnings = [], []
    existing_params = {}
    for a in cfg.get("attach") or []:
        if a.get("peripheral") in _SV_PERIPHERALS:
            existing_params[a["peripheral"]] = a.get("params") or {}

    # 1. peripherals BGM does not instantiate here -> remove
    for pid in sorted(_SV_PERIPHERALS - set(derived)):
        for i, j, _ind in reversed(_find_attach_blocks(lines, pid)):
            del lines[i:j]
            changes.append("removed {} (BGM does not instantiate it in this variant)".format(pid))

    # 1b. BGM feeds `mic` from the INMP441 and ties the on-board PDM microphone
    #     off (`M_CLK = 0`, `M_LRSEL = 0` on the Nexys boards); audio_in is an
    #     exclusive capability, so the PDM attach has to go.
    if "inmp441_i2s_mic" in derived and derived["inmp441_i2s_mic"][0]:
        for i, j, _ind in reversed(_find_attach_blocks(lines, "pdm_mic")):
            del lines[i:j]
            changes.append("removed pdm_mic (BGM drives mic from the INMP441 and ties the PDM mic off)")

    # 2. peripherals BGM instantiates -> replace or append
    comment = "binds from BGM {}/board_specific_top.sv".format(os.path.basename(vdir))
    for pid, (binds, notes) in sorted(derived.items()):
        warnings.extend("{}: {}".format(pid, n) for n in notes)
        if not binds:
            continue
        block = _render_attach(pid, existing_params.get(pid, {}), binds, comment, item_indent)
        blocks = _find_attach_blocks(lines, pid)
        if blocks:
            i, j, _ind = blocks[0]
            lines[i:j] = block
            for i2, j2, _ in reversed(blocks[1:]):
                del lines[i2:j2]
            changes.append("{} binds {}".format(pid, binds))
        else:
            # append after the last attach item
            idx = len(lines)
            for k in range(len(lines) - 1, -1, -1):
                if lines[k].startswith(item_indent + "  ") or re.match(r"^ {2,4}- peripheral:", lines[k]):
                    idx = k + 1
                    break
            lines[idx:idx] = block
            changes.append("added {} {}".format(pid, binds))

    # 2b. a header passthrough (pmod_12pin) whose pins a derived driver bank
    #     owns: BGM comments those header pins out (tang_primer_25k_pmod_hdmi
    #     uses PMOD_0 as the DVI Pmod), so the passthrough goes.
    driver_pins = set()
    for pid, (binds, _notes) in derived.items():
        for ref in binds.values():
            for one in (ref if isinstance(ref, list) else [ref]):
                driver_pins.update(_pins_of_ref(pinmap, one))
    if driver_pins:
        try:
            cfg_now = yaml.safe_load("\n".join(lines))["Configuration"]
        except Exception:
            cfg_now = cfg
        for a in cfg_now.get("attach") or []:
            if a.get("peripheral") != "pmod_12pin":
                continue
            ref = (a.get("bind") or {}).get("io")
            header_pins = _pins_of_ref(pinmap, ref) if isinstance(ref, str) else set()
            # only when the driver owns the whole header; a partial overlap
            # (TM1638 on three Pmod pins) is handled bit-wise by codegen
            if not header_pins or not header_pins <= driver_pins:
                continue
            for i, j, _ in reversed(_find_attach_blocks(lines, "pmod_12pin")):
                if re.search(r":\s*\"?%s\b" % re.escape(ref), "\n".join(lines[i:j])):
                    del lines[i:j]
                    changes.append("removed pmod_12pin on {} (its pins carry {} in BGM)".format(
                        ref, ", ".join(sorted(pid for pid, (b, _n) in derived.items()
                                              if any(_pins_of_ref(pinmap, r) & _pins_of_ref(pinmap, ref)
                                                     for v in b.values() for r in (v if isinstance(v, list) else [v]))))))
                    break

    # 3. a plain input passthrough (buttons/switches) sharing a pin with a
    #    driver peripheral: BGM's variant gives the pin to the module.
    text = "\n".join(lines)
    try:
        cfg2 = yaml.safe_load(text)["Configuration"]
    except Exception as exc:
        return "ERROR: rewrite produced invalid YAML: {}".format(exc)
    resolved = None
    try:
        peripherals = config_init.read_peripherals()
        try:
            board = config_init.resolve_configuration(cfg2["id"])["board"]
        except Exception:
            board = {}
        fake = {"configuration": cfg2, "board": board, "board_pinmap": pinmap,
                "toolchain": {"Id": cfg2["toolchain"]},
                "peripherals": [{"peripheral_id": a["peripheral"], "peripheral": peripherals[a["peripheral"]],
                                 "params": a.get("params") or {}, "bind": a.get("bind") or {}}
                                for a in cfg2.get("attach") or []]}
        resolved = fake
    except KeyError:
        pass
    if resolved is not None:
        for problem in codegen.validate_configuration(resolved):
            m = re.search(r"port bit (\S+) is driven/bound by 2 peripherals: (\w+)#(\d+), (\w+)#(\d+)", problem)
            if not m:
                continue
            pair = [(m.group(2), int(m.group(3))), (m.group(4), int(m.group(5)))]
            passthrough = [(pid, i) for pid, i in pair if pid in ("button_array", "sw_bank")]
            driver = [(pid, i) for pid, i in pair if pid in _SV_PERIPHERALS]
            if passthrough and driver:
                pid, _i = passthrough[0]
                for i, j, _ in reversed(_find_attach_blocks(lines, pid)):
                    if cfg2["attach"][_i].get("bind") and any(
                            str(v) in "\n".join(lines[i:j]) for v in cfg2["attach"][_i]["bind"].values()):
                        del lines[i:j]
                        changes.append("removed {} (its pin {} belongs to {} in BGM)".format(pid, m.group(1), driver[0][0]))
                        break
        text = "\n".join(lines)

    if warnings:
        changes.append("WARNINGS: " + "; ".join(warnings))
    if not text.endswith("\n"):
        text += "\n"
    if text == original:
        return "unchanged" if not warnings else "unchanged; " + "; ".join(warnings)
    if not dry_run:
        open(path, "w", encoding="utf-8").write(text)
    return "; ".join(changes)


# ---------------------------------------------------------------------------
# --polarity: bank-level active/mirror attributes from BGM's inversions
# ---------------------------------------------------------------------------

# Passthrough peripherals whose banks carry the user-visible polarity.
_POLARITY_PERIPHERALS = {"button_array", "sw_bank", "led_bank", "rgb_led", "reset_button"}


def derive_bank_polarity(vdir, pinmap, cfg):
    """{bank: {"active": "low"|"high", "mirror": bool, "ports": [...]}} for the
    banks the configuration's passthrough peripherals bind, from the ports BGM
    inverts / bit-swaps in this variant's active branch (by pin identity)."""
    text = bgm_oracle.preprocess_variant(vdir).text
    inv = bgm_oracle.port_polarity(text)
    body = text.split(");", 1)[1] if ");" in text else text
    referenced = set(re.findall(r"\b([A-Za-z_]\w*)\b", body))
    sig_pins = _bgm_signal_pins(vdir)
    rev = _pin_to_ref(pinmap)
    # BGM port -> banks (a port like KEY[3:0] covers several pins). A port the
    # active branch never references (BTN_N on a TM1638 variant) abstains.
    port_banks = {}
    for key, pin in sig_pins.items():
        port = key.split("[", 1)[0]
        if port not in referenced and port not in inv:
            continue
        ref = rev.get(pin)
        if ref is None:
            continue
        bank = re.split(r"[.\[]", ref, 1)[0]
        port_banks.setdefault(port, set()).add(bank)
    wanted = set()
    for a in cfg.get("attach") or []:
        if a.get("peripheral") in _POLARITY_PERIPHERALS:
            for ref in (a.get("bind") or {}).values():
                for one in (ref if isinstance(ref, list) else [ref]):
                    if isinstance(one, str):
                        wanted.add(re.split(r"[.\[]", one, 1)[0])
    out = {}
    for port, banks in port_banks.items():
        for bank in banks & wanted:
            d = out.setdefault(bank, {"inverted": set(), "mirror": False, "ports": []})
            p = inv.get(port, {"inverted": False, "mirrored": False})
            d["inverted"].add(bool(p["inverted"]))
            d["mirror"] = d["mirror"] or bool(p["mirrored"])
            d["ports"].append(port)
    result = {}
    for bank, d in out.items():
        if len(d["inverted"]) > 1:
            result[bank] = {"active": None, "mirror": d["mirror"], "ports": sorted(d["ports"]),
                            "note": "BGM inverts some of this bank's ports but not others"}
        else:
            result[bank] = {"active": "low" if True in d["inverted"] else "high",
                            "mirror": d["mirror"], "ports": sorted(d["ports"])}
    return result


def _set_bank_attr(pinmap_text, bank, attr, value):
    """Insert or replace `attr: value` on a bank (inline or block form)."""
    val = "true" if value is True else ("false" if value is False else str(value))
    inline = re.compile(r"^(    {}:\s*\{{)([^}}]*)(\}})".format(re.escape(bank)), re.M)
    m = inline.search(pinmap_text)
    if m:
        body = m.group(2)
        if re.search(r"\b{}:".format(attr), body):
            body = re.sub(r"\b{}:\s*[^,}}]+".format(attr), "{}: {}".format(attr, val), body)
        else:
            body = body.rstrip() + ", {}: {} ".format(attr, val)
        return pinmap_text[:m.start()] + m.group(1) + body + m.group(3) + pinmap_text[m.end():]
    block = re.compile(r"^(    {}:\s*\n)((?:      .*\n|        .*\n)*)".format(re.escape(bank)), re.M)
    m = block.search(pinmap_text)
    if not m:
        return None
    body = m.group(2)
    if re.search(r"^      {}:".format(attr), body, re.M):
        body = re.sub(r"^      {}:.*$".format(attr), "      {}: {}".format(attr, val), body, flags=re.M)
    else:
        body = "      {}: {}\n".format(attr, val) + body
    return pinmap_text[:m.start()] + m.group(1) + body + pinmap_text[m.end():]


def _set_attach_param(lines, pid, bank, key, value):
    """Set `params.<key>: <value>` on the attach of `pid` whose bind mentions
    `bank`; returns True when the text changed."""
    for i, j, ind in _find_attach_blocks(lines, pid):
        block = lines[i:j]
        if bank is not None and not any(re.search(r":\s*\"?%s\b" % re.escape(bank), l) for l in block):
            continue
        key_indent = ind + "  "
        val = "true" if value is True else ("false" if value is False else str(value))
        pidx = next((k for k, l in enumerate(block) if l.startswith(key_indent + "params:")), None)
        if pidx is None:
            lines[i + 1:i + 1] = [key_indent + "params:", key_indent + "  {}: {}".format(key, val)]
            return True
        for k in range(pidx + 1, len(block)):
            if not block[k].startswith(key_indent + "  "):
                break
            m = re.match(r"^%s  %s:\s*(\S+)" % (re.escape(key_indent), re.escape(key)), block[k])
            if m:
                if m.group(1) == val:
                    return False
                lines[i + k] = "{}  {}: {}".format(key_indent, key, val)
                return True
        lines[i + pidx + 1:i + pidx + 1] = [key_indent + "  {}: {}".format(key, val)]
        return True
    return False


def _del_attach_param(lines, pid, key):
    """Remove `params.<key>` from every attach of `pid` (and an emptied
    `params:`); returns True when the text changed."""
    changed = False
    for i, j, ind in reversed(_find_attach_blocks(lines, pid)):
        key_indent = ind + "  "
        pidx = next((k for k in range(i, j) if lines[k].startswith(key_indent + "params:")), None)
        if pidx is None:
            continue
        k = pidx + 1
        while k < j and lines[k].startswith(key_indent + "  "):
            if re.match(r"^%s  %s:" % (re.escape(key_indent), re.escape(key)), lines[k]):
                del lines[k]
                j -= 1
                changed = True
                continue
            k += 1
        if k == pidx + 1:                      # params: is empty now
            del lines[pidx]
            changed = True
    return changed


# ---------------------------------------------------------------------------
# --clock-tree: PLL pixel clock, lab clock and LCD backlight/init from BGM
# ---------------------------------------------------------------------------

_LAB_CLOCK_LINE = "  lab_clock: pixel   # BGM: `localparam lab_mhz = pixel_mhz` (tools/sync_from_bgm.py --clock-tree)"


def _bgm_pixel_mhz(outs):
    """Pick BGM's pixel clock among the used PLL outputs [(net, mhz, via)]:
    the one on an LCD/pixel-named net, else CLKOUTD over CLKOUT, else the
    only one."""
    if not outs:
        return None
    named = [o for o in outs if re.search(r"LCD|_CK\b|pixel", o[0], re.I)]
    if len(named) == 1:
        return named[0][1]
    d = [o for o in outs if o[2] == "clkoutd"]
    if len(d) == 1:
        return d[0][1]
    return outs[0][1] if len(outs) == 1 else None


def _fmt_outs(outs):
    return ", ".join("{} = {:g} MHz ({})".format(n, round(m, 6), via) for n, m, via in outs)


def _aux_expr_to_ref(expr):
    e = expr.replace(" ", "")
    return {"1'b0": "const.0", "1'b1": "const.1", "~rst": "context.rst_n", "rst": "context.rst",
            "rst_n": "context.rst_n"}.get(e)


def apply_clock_tree(path, dry_run):
    """Per configuration, from the BGM variant's preprocessed top and PLL
    wrapper: (1) `lab_clock: pixel` when BGM runs the lab on the PLL clock;
    (2) `params.clock_<name>_mhz` on peripherals whose `clocks:` default
    differs from BGM's computed PLL output (9K 800x480: 32.4 MHz via CLKOUTD,
    Tang Nano 20K alt: 48.9375 MHz); (3) `params.bl` / `params.init` from
    BGM's `assign LCD_BL = ...` on the same physical pins. Skips the pixel
    clock when BGM builds a different screen size than the configuration
    attaches (BGM's tang_nano_9k_lcd_480_272_*_yosys define USE_LCD_800_480)."""
    original = open(path, encoding="utf-8").read()
    text = original
    cfg = yaml.safe_load(text)["Configuration"]
    cid = cfg["id"]
    vdir = bgm_oracle.variant_dir_for(cid, cfg["board"])
    if vdir is None:
        return "skip (no BGM variant)"
    try:
        resolved = config_init.resolve_configuration(cid)
    except Exception as exc:          # a configuration the loader rejects
        return "skip (resolve: {})".format(str(exc).splitlines()[0][:80])
    from tools import codegen
    pp = bgm_oracle.preprocess_variant(vdir)
    t = pp.text
    lines = text.split("\n")
    changes = []

    declared = {}
    for a in resolved["peripherals"]:
        for c in a["peripheral"].get("clocks") or []:
            declared.setdefault(c["name"], []).append(a)

    # 1. lab clock
    want = bgm_oracle.lab_clock_source(t)
    have = cfg.get("lab_clock")
    idx = next((k for k, l in enumerate(lines) if re.match(r"^  lab_clock:", l)), None)
    if want == "pixel":
        if "pixel" not in declared:
            changes.append("WARNING: BGM runs the lab on the pixel clock ({} MHz) but no attached peripheral "
                           "declares clock 'pixel' (HDMI: PLAN.md P3.2)".format(bgm_oracle.lab_mhz(t)))
        elif have != "pixel":
            if idx is not None:
                lines[idx] = _LAB_CLOCK_LINE
            else:
                hdr = next((k for k, l in enumerate(lines) if re.match(r"^  (toolchain|part):", l)), None)
                while hdr is not None and hdr + 1 < len(lines) and re.match(r"^  (toolchain|part):", lines[hdr + 1]):
                    hdr += 1
                if hdr is None:
                    changes.append("WARNING: no toolchain: line; lab_clock not inserted")
                else:
                    lines[hdr + 1:hdr + 1] = [_LAB_CLOCK_LINE]
            changes.append("lab_clock: pixel ({} MHz)".format(bgm_oracle.lab_mhz(t)))
    elif have is not None and idx is not None:
        del lines[idx]
        changes.append("drop lab_clock (BGM lab runs on the board clock)")

    # 2. pixel clock frequency
    outs, unmodelled = bgm_oracle.pll_outputs(vdir, t, pp.files)
    if "serial" in declared and not unmodelled:
        # HDMI: BGM's serial clock is 5x (DVI_TX IP, DDR) or 10x (dvi_top,
        # SDR) the pixel clock; ours is always 10x, so copy the pixel clock,
        # not the serial one. 124.875 MHz on the Tang Nano 20K -> 24.975 MHz
        # pixel -> serial 249.75 MHz here.
        # the PLL output the transmitter's serial-clock port takes (colorlight:
        # `hdmi i_hdmi (.clk_TMDS (clk_250MHz), ...)`), else by net name
        tmds_nets = set()
        for module in _TMDS_MODULES:
            for inst in bgm_oracle.instantiations(t, module):
                for port, expr in inst["ports"]:
                    if re.search(r"serial|tmds|x5|5x", port, re.I) and re.search(r"clk|clock", port, re.I) and expr.strip():
                        tmds_nets.add(expr.strip())
        serial_outs = [o for o in outs if o[0] in tmds_nets]
        if not serial_outs:
            serial_outs = [o for o in outs if re.search(r"serial|x5|TMDS", o[0], re.I)]
        if not serial_outs and len(outs) == 1:
            serial_outs = list(outs)        # the only PLL output feeds the transmitter (25K: vga_in_clk)
        pix_param = re.search(r"\bpixel_mhz\s*=\s*([0-9.]+)", t)
        bgm_pixel = bgm_serial = ratio = None
        if len(serial_outs) == 1:
            bgm_serial = serial_outs[0][1]
            if pix_param:
                ratio = int(round(bgm_serial / float(pix_param.group(1))))
            else:
                # no pixel_mhz parameter (marsohod3gw2): 5x (DDR) or 10x (SDR),
                # whichever gives a VGA-class pixel clock
                ratio = next((k for k in (5, 10) if 20.0 <= bgm_serial / k <= 40.0), 0)
            if ratio in (5, 10):
                bgm_pixel = bgm_serial / ratio
        elif not serial_outs:
            # No PLL in the top (Tang Nano 4K: the DVI_TX IP makes its own
            # serial clock) and the pixel clock is the board clock itself.
            for inst in bgm_oracle.instantiations(t, "DVI_TX_Top"):
                if dict(inst["ports"]).get("I_rgb_clk", "").strip().lower() in ("clk", "clk_in"):
                    bgm_pixel, bgm_serial, ratio = bgm_oracle.clk_mhz(t), None, None
        if bgm_pixel is not None:
            if True:
                for a in declared["serial"]:
                    pid = a["peripheral_id"]
                    c = next(c for c in a["peripheral"]["clocks"] if c["name"] == "serial")
                    div = next((int(c2["divide"]) for c2 in a["peripheral"]["clocks"]
                                if c2.get("from") == "serial"), 10)
                    want = round(bgm_pixel * div, 6)
                    if abs(want - float(c["mhz"])) > 1e-6:
                        if _set_attach_param(lines, pid, None, "clock_serial_mhz", "{:g}".format(want)):
                            changes.append("{}: clock_serial_mhz = {:g} (BGM pixel {:g} MHz{})".format(
                                pid, want, bgm_pixel,
                                " = serial {:g} / {}".format(bgm_serial, ratio) if bgm_serial else " = the board clock"))
                    elif _del_attach_param(lines, pid, "clock_serial_mhz"):
                        changes.append("{}: drop clock_serial_mhz (BGM pixel {:g} MHz = default)".format(pid, bgm_pixel))
        elif bgm_serial is not None:
            changes.append("WARNING: BGM serial clock {:g} MHz has no 5x/10x pixel clock; not copied".format(bgm_serial))
    if "pixel" in declared and declared["pixel"][0]["peripheral"].get("clocks") and \
            any(c.get("from") for a in declared["pixel"] for c in a["peripheral"]["clocks"] if c["name"] == "pixel"):
        pass                                # derived pixel clock: handled through `serial`
    elif "pixel" in declared:
        size = bgm_oracle.screen_size(t)
        plans = codegen.build_capability_plans(resolved)
        ours = (plans["screen"].params.get("width"), plans["screen"].params.get("height"))
        mhz = _bgm_pixel_mhz(outs)
        if unmodelled:
            changes.append("note: BGM PLL {} not modelled; pixel clock left at the peripheral default".format(unmodelled))
        elif size is not None and size != ours:
            changes.append("WARNING: BGM builds {}x{} but the configuration attaches {}x{}; pixel clock "
                           "({} MHz) not copied".format(size[0], size[1], ours[0], ours[1], mhz))
        elif mhz is None:
            changes.append("WARNING: cannot tell which PLL output is the pixel clock: {}".format(_fmt_outs(outs)))
        else:
            mhz = round(mhz, 6)
            for a in declared["pixel"]:
                pid = a["peripheral_id"]
                default = next(float(c["mhz"]) for c in a["peripheral"]["clocks"] if c["name"] == "pixel")
                if mhz > 4 * default or mhz < default / 4:
                    # BGM tang_nano_9k_lcd_480_272_no_tm1638_yosys ships the
                    # 800x480 gowin_rpll.v but its top takes the 480x272 branch
                    # (CLKOUT = 129.6 MHz on LARGE_LCD_CK): an upstream bug,
                    # not a frequency to copy.
                    changes.append("WARNING: BGM pixel clock {:g} MHz is implausible for {} (default {:g} MHz); "
                                   "not copied (upstream PLL/branch mismatch?)".format(mhz, pid, default))
                    continue
                if abs(mhz - default) > 1e-6:
                    if _set_attach_param(lines, pid, None, "clock_pixel_mhz", "{:g}".format(mhz)):
                        changes.append("{}: clock_pixel_mhz = {:g} (BGM {})".format(pid, mhz, _fmt_outs(outs)))
                elif _del_attach_param(lines, pid, "clock_pixel_mhz"):
                    changes.append("{}: drop clock_pixel_mhz (BGM uses the default {:g})".format(pid, default))

    # 3. LCD backlight / init from BGM's assigns on the same pins
    assigns = bgm_oracle.port_assigns(t)
    if assigns:
        pinmap = resolved["board_pinmap"]
        ref_to_pin = {ref: pin for pin, ref in _pin_to_ref(pinmap).items()}
        pin_to_sig = {pin: sig for sig, pin in _bgm_signal_pins(vdir).items()}
        for a in resolved["peripherals"]:
            pid = a["peripheral_id"]
            params_def = a["peripheral"].get("parameters") or {}
            for sig in ("bl", "init"):
                if sig not in params_def:
                    continue
                ref = (a.get("bind") or {}).get(sig)
                if not isinstance(ref, str):
                    continue
                bgm_sig = pin_to_sig.get(ref_to_pin.get(ref))
                expr = assigns.get(bgm_sig) if bgm_sig else None
                if expr is None:
                    continue
                want_ref = _aux_expr_to_ref(expr)
                if want_ref is None:
                    changes.append("WARNING: {} = {} in BGM has no ref equivalent".format(bgm_sig, expr))
                    continue
                default = params_def[sig].get("default")
                if want_ref == default:
                    if _del_attach_param(lines, pid, sig):
                        changes.append("{}: drop {} (default {})".format(pid, sig, default))
                elif _set_attach_param(lines, pid, None, sig, want_ref):
                    changes.append("{}: {} = {} (BGM: assign {} = {})".format(pid, sig, want_ref, bgm_sig, expr))

    text = "\n".join(lines)
    if not text.endswith("\n"):
        text += "\n"
    if text == original:
        return "; ".join(changes) if changes else "unchanged"
    if not dry_run:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        config_init.clear_cache()
    return "; ".join(changes)


def apply_polarity(paths, dry_run):
    """`active` is a board fact when every BGM variant of the board agrees ->
    written as a bank attribute in the pinmap. When BGM's variants disagree
    (Tang Nano 20K: `| KEY` in the TM1638 branch, `~ KEY` in the other) each
    configuration gets `params.active` so it still matches its own oracle, and
    the disagreement is reported for upstream. `mirror` (BGM `SWAP_BITS` under
    REVERSE_LED, defined per variant) is always a per-configuration param."""
    per_board, per_cfg = {}, {}
    for path in paths:
        cfg = yaml.safe_load(open(path, encoding="utf-8"))["Configuration"]
        vdir = bgm_oracle.variant_dir_for(cfg["id"], cfg["board"])
        if vdir is None:
            continue
        pinmap = config_init.read_board_pinmap(cfg["board"]) or {}
        config_init._apply_pin_overrides(cfg["id"], cfg, pinmap)
        derived = derive_bank_polarity(vdir, pinmap, cfg)
        per_cfg[cfg["id"]] = (path, cfg, derived)
        for bank, d in derived.items():
            per_board.setdefault(cfg["board"], {}).setdefault(bank, []).append((cfg["id"], d))

    results = {}
    conflicts = {}          # (board, bank) -> True
    for board, banks in sorted(per_board.items()):
        pm_path = _pinmap_path(board)
        pinmap = config_init.read_board_pinmap(board) or {}
        text = open(pm_path, encoding="utf-8").read()
        notes = []
        for bank, entries in sorted(banks.items()):
            actives = {d["active"] for _c, d in entries}
            if None in actives:
                notes.append("MIXED {}: BGM inverts only some of its ports ({}); left as is"
                             .format(bank, sorted({p for _c, d in entries for p in d["ports"]})))
                conflicts[(board, bank)] = True
                continue
            if len(actives) > 1:
                notes.append("VARIANTS DISAGREE {}: {} -> per-configuration active".format(
                    bank, {c: d["active"] for c, d in entries}))
                conflicts[(board, bank)] = True
                continue
            (active,) = actives
            cur = (pinmap.get("pinBanks") or {}).get(bank) or {}
            if active == "low" and cur.get("active") != "low":
                new = _set_bank_attr(text, bank, "active", "low")
                if new:
                    text = new
                    notes.append("{} <- active: low (BGM ports {})".format(bank, entries[0][1]["ports"]))
            elif active == "high" and cur.get("active") == "low":
                new = _set_bank_attr(text, bank, "active", "high")
                if new:
                    text = new
                    notes.append("{} <- active: high".format(bank))
        if not dry_run and text != open(pm_path, encoding="utf-8").read():
            open(pm_path, "w", encoding="utf-8").write(text)
            config_init.clear_cache()
        results[board] = "; ".join(notes) if notes else "unchanged"

    # per-configuration: mirror always, active only for disagreeing banks
    for cid, (path, cfg, derived) in sorted(per_cfg.items()):
        original = open(path, encoding="utf-8").read()
        lines = original.split("\n")
        notes = []
        for bank, d in derived.items():
            pid = next((a["peripheral"] for a in cfg.get("attach") or []
                        if a.get("peripheral") in _POLARITY_PERIPHERALS
                        and any(bank in str(v) for v in (a.get("bind") or {}).values())), None)
            if pid is None:
                continue
            if d["mirror"] and _set_attach_param(lines, pid, bank, "mirror", True):
                notes.append("{}.{} mirror: true".format(pid, bank))
            if (cfg["board"], bank) in conflicts and d["active"] is not None:
                if _set_attach_param(lines, pid, bank, "active", d["active"]):
                    notes.append("{}.{} active: {} (variant-specific)".format(pid, bank, d["active"]))
        text = "\n".join(lines)
        if text != original:
            if not dry_run:
                open(path, "w", encoding="utf-8").write(text)
            results[cid] = "; ".join(notes)
    return results


# ---------------------------------------------------------------------------
# --gowin-options: set_device args and set_option flags from BGM's .tcl
# ---------------------------------------------------------------------------

_GOWIN_TOOLCHAINS = {"gowin_eda", "gowin_standard", "nextpnr_apicula"}


def _render_gowin_block(set_device, options, reason=None):
    lines = ["  # Gowin tool settings from BGM's board_specific.tcl (tools/sync_from_bgm.py --gowin-options):",
             "  # set_device args verbatim (part, -name, -device_version) and the",
             "  # set_option -use_*_as_gpio flags that free configuration pins for I/O.",
             "  toolchain_options:",
             "    gowin:"]
    if set_device:
        lines.append('      set_device: "{}"'.format(set_device.replace('"', '\\"')))
    if reason:
        lines.append("      set_device_reason: {}".format(reason))
    lines.append("      options: [{}]".format(", ".join(o.lstrip("-") for o in options)))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# --quartus-options / --yosys-options: board-level tool settings from BGM
# ---------------------------------------------------------------------------
# Quartus project settings that shape the bitstream / pin behaviour (the
# nCEO dual-purpose pin used as regular I/O, unused-pin state, device I/O
# default, configuration scheme). Synthesis-tuning, IP, EDA and file entries
# stay out.
_QSF_GLOBAL_KEEP = re.compile(
    r"^(RESERVE_\w+|CYCLONEII_RESERVE_\w+|STRATIX_DEVICE_IO_STANDARD|\w*CONFIGURATION_SCHEME|USE_CONF_DONE"
    r"|NOMINAL_CORE_SUPPLY_VOLTAGE|ON_CHIP_BITSTREAM_DECOMPRESSION|CYCLONE_OPTIMIZATION_TECHNIQUE"
    r"|\w*_CONFIGURATION_DEVICE|USE_CONFIGURATION_DEVICE|ENABLE_\w+_PIN|ENABLE_INIT_DONE_OUTPUT"
    r"|CRC_ERROR_OPEN_DRAIN|VCCA_USER_VOLTAGE|ACTIVE_SERIAL_CLOCK|GENERATE_RBF_FILE|PWRMGT_\w+|USE_PWRMGT_\w+"
    r"|VCCIO_\w+|INTERNAL_FLASH_UPDATE_MODE|AUTO_RESTART_CONFIGURATION|ENABLE_OCT_DONE|FORCE_CONFIGURATION_VCCIO)$")


def _bgm_qsf_globals(vdir):
    """['NAME VALUE', ...] global assignments of the variant's QSF files that
    _QSF_GLOBAL_KEEP admits, in file order without duplicates."""
    from tools import import_constraints as ic
    out = []
    for path in ic.find_all_constraint_files(vdir):
        if not path.lower().endswith(".qsf"):
            continue
        for line in open(path, encoding="utf-8", errors="replace"):
            m = re.match(r"^\s*set_global_assignment\s+-name\s+(\w+)\s+(.+?)\s*$", line.split("#", 1)[0])
            if m and _QSF_GLOBAL_KEEP.match(m.group(1)):
                entry = "{} {}".format(m.group(1), " ".join(m.group(2).split()))
                if entry not in out:
                    out.append(entry)
    return out


def _bgm_board_info(vdir):
    """BGM's board_info.source_bash for the open flows as a dict: the yosys
    synth flags after the command (`synth_ice40 -dsp -noabc9` ->
    synth_options [dsp, noabc9]) and the openFPGALoader settings its
    configure_fpga_yosys uses (BOARD -> loader_board, CABLE -> loader_cable,
    FTDI_CHANNEL -> loader_ftdi_channel). None without the file."""
    path = os.path.join(vdir, "board_info.source_bash")
    if not os.path.exists(path):
        return None
    text = open(path, encoding="utf-8", errors="replace").read()
    out = OrderedDict()
    m = re.search(r'^\s*SYNTH_CMD\s*=\s*"([^"]*)"', text, re.M)
    if m:
        out["synth_options"] = [t.lstrip("-") for t in m.group(1).split()[1:]]
    for var, key in (("BOARD", "loader_board"), ("CABLE", "loader_cable"), ("FTDI_CHANNEL", "loader_ftdi_channel")):
        m = re.search(r'^\s*' + var + r'\s*=\s*"?([^"\n]*)"?\s*$', text, re.M)
        if m and m.group(1).strip():
            out[key] = m.group(1).strip()
    return out or None


def _set_toolchain_block(text, key, body_lines, comment):
    """Insert or replace the `key:` sub-block of a pinmap's toolchain_options."""
    sub = "    {}:\n".format(key) + "".join("      {}\n".format(l) for l in body_lines)
    if re.search(r"^  toolchain_options:", text, re.M):
        new, n = re.subn(r"(?m)^    {}:\n(?:^      .*\n)*".format(re.escape(key)), sub.replace("\\", "\\\\"), text, count=1)
        if n == 1:
            return new
        return re.sub(r"^(  toolchain_options:\n)", lambda m: m.group(1) + sub, text, count=1, flags=re.M)
    block = "".join("  # {}\n".format(c) for c in comment) + "  toolchain_options:\n" + sub
    return re.sub(r"^(  pinBanks:)", lambda m: block + m.group(1), text, count=1, flags=re.M)


def _del_toolchain_block(text, key):
    return re.sub(r"(?m)^    {}:\n(?:^      .*\n)*".format(re.escape(key)), "", text, count=1)


def _apply_toolchain_option(paths, dry_run, toolchains, key, derive, render, current, comment):
    per_board = {}
    for path in paths:
        cfg = yaml.safe_load(open(path, encoding="utf-8"))["Configuration"]
        if cfg.get("toolchain") not in toolchains:
            continue
        vdir = bgm_oracle.variant_dir_for(cfg["id"], cfg["board"])
        if vdir is None:
            continue
        got = derive(vdir)
        if got is None:
            continue
        per_board.setdefault(cfg["board"], set()).add(tuple(got))
    results = {}
    for board, variants in sorted(per_board.items()):
        if len(variants) > 1:
            results[board] = "CONFLICT between BGM variants: {}".format(sorted(variants))
            continue
        (want,) = variants
        pm_path = _pinmap_path(board)
        pinmap = config_init.read_board_pinmap(board) or {}
        if list(current(pinmap)) == list(want):
            results[board] = "already"
            continue
        text = open(pm_path, encoding="utf-8").read()
        new = _set_toolchain_block(text, key, render(want), comment) if want else _del_toolchain_block(text, key)
        if new != text and not dry_run:
            open(pm_path, "w", encoding="utf-8").write(new)
            config_init.clear_cache()
        results[board] = "{} -> {}".format(list(current(pinmap)) or "none", list(want) or "none")
    return results


def apply_quartus_options(paths, dry_run):
    return _apply_toolchain_option(
        paths, dry_run, _QUARTUS_TOOLCHAINS, "quartus", _bgm_qsf_globals,
        lambda want: ["global_assignments:"] + ['  - {}'.format(_yaml_str(g)) for g in want],
        lambda pm: ((pm.get("toolchain_options") or {}).get("quartus") or {}).get("global_assignments") or [],
        ["Quartus project settings from BGM's board_specific.qsf (tools/sync_from_bgm.py --quartus-options):",
         "dual-purpose pin reservation, unused-pin state, device I/O default."])


def _render_yosys_block(info):
    lines = []
    if "synth_options" in info:
        lines.append("synth_options: [{}]".format(", ".join(info["synth_options"])))
    for key in ("loader_board", "loader_cable", "loader_ftdi_channel"):
        if key in info:
            lines.append("{}: {}".format(key, _yaml_str(str(info[key]))))
    return lines


def _current_yosys(pinmap):
    cur = (pinmap.get("toolchain_options") or {}).get("yosys") or {}
    out = OrderedDict()
    if cur.get("synth_options") is not None:
        out["synth_options"] = [str(o) for o in cur["synth_options"]]
    for key in ("loader_board", "loader_cable", "loader_ftdi_channel"):
        if cur.get(key) not in (None, ""):
            out[key] = str(cur[key])
    return out


def apply_yosys_options(paths, dry_run):
    return _apply_toolchain_option(
        paths, dry_run, _YOSYS_TOOLCHAINS, "yosys",
        lambda vdir: (lambda d: tuple(sorted((k, tuple(v) if isinstance(v, list) else v) for k, v in d.items())))(_bgm_board_info(vdir))
        if _bgm_board_info(vdir) else None,
        lambda want: _render_yosys_block(OrderedDict((k, list(v) if isinstance(v, tuple) else v) for k, v in want)),
        lambda pm: tuple(sorted((k, tuple(v) if isinstance(v, list) else v) for k, v in _current_yosys(pm).items())),
        ["yosys synth flags and openFPGALoader settings from BGM's board_info.source_bash",
         "(SYNTH_CMD, BOARD, CABLE, FTDI_CHANNEL; tools/sync_from_bgm.py --yosys-options)."])


def _yaml_str(v):
    return '"{}"'.format(v.replace("\\", "\\\\").replace('"', '\\"'))


# ---------------------------------------------------------------------------
# --iotypes: Gowin IO_TYPE per pin exactly as BGM's CST files state them
# ---------------------------------------------------------------------------

def _bgm_iotypes(vdir):
    """{normalized pin: IO_TYPE} from every constraint file of the variant
    (only pins BGM gives an IO_TYPE; most Gowin CSTs give none)."""
    from tools import import_constraints as ic
    out = {}
    for path in ic.find_all_constraint_files(vdir):
        try:
            signals, _fmt = ic.parse_file(path)
        except Exception:
            continue
        for _name, entry in signals.items():
            if entry.get("iostandard"):
                t = entry["iostandard"]
                out.setdefault(_norm_pin(entry["pin"]), t.upper() if _fmt == "cst" else t)
    return out


_QUARTUS_TOOLCHAINS = {"quartus2", "quartus_prime", "quartus_prime_lite", "quartus_prime_standard", "quartus_prime_pro"}
_YOSYS_TOOLCHAINS = {"nextpnr_icestorm", "nextpnr_trellis", "nextpnr_apicula"}


def _bgm_qsf_default(vdir):
    """BGM's project-wide I/O standard (`IO_STANDARD ... -to *` or
    STRATIX_DEVICE_IO_STANDARD) from the variant's QSF files, or None."""
    from tools import import_constraints as ic
    for path in ic.find_all_constraint_files(vdir):
        if not path.lower().endswith(".qsf"):
            continue
        text = open(path, encoding="utf-8", errors="replace").read()
        m = re.search(r'IO_STANDARD\s+"([^"]+)"\s+-to\s+\*\s*$', text, re.M)
        if m:
            return m.group(1)
        m = re.search(r'STRATIX_DEVICE_IO_STANDARD\s+"([^"]+)"', text)
        if m:
            return m.group(1)
    return None


def _del_bank_attr(pinmap_text, bank, attr):
    """Remove `attr` (scalar line or block) from a bank; returns new text."""
    inline = re.compile(r"^(    {}:\s*\{{)([^}}]*)(\}})".format(re.escape(bank)), re.M)
    m = inline.search(pinmap_text)
    if m:
        body = re.sub(r",?\s*\b{}:\s*[^,}}]+".format(re.escape(attr)), "", m.group(2))
        body = body.strip().strip(",").strip()
        return pinmap_text[:m.start()] + m.group(1) + " " + body + " " + m.group(3) + pinmap_text[m.end():]
    block = re.compile(r"^(    {}:\s*\n)((?:      .*\n|        .*\n)*)".format(re.escape(bank)), re.M)
    m = block.search(pinmap_text)
    if not m:
        return pinmap_text
    body = re.sub(r"^      {}:.*\n(?:^        .*\n)*".format(re.escape(attr)), "", m.group(2), flags=re.M)
    return pinmap_text[:m.start()] + m.group(1) + body + pinmap_text[m.end():]


def _set_bank_overrides(pinmap_text, bank, overrides):
    text = _del_bank_attr(pinmap_text, bank, "overrides")
    if not overrides:
        return text
    block = re.compile(r"^(    {}:\s*\n)((?:      .*\n|        .*\n)*)".format(re.escape(bank)), re.M)
    m = block.search(text)
    if not m:
        return None
    lines = "      overrides:\n" + "".join('        "{}": {}\n'.format(pin, t) for pin, t in sorted(overrides.items()))
    return text[:m.start()] + m.group(1) + m.group(2) + lines + text[m.end():]


def _set_defaults_iostandard(pinmap_text, value):
    m = re.search(r"^  defaults:\s*\n((?:    .*\n)*)", pinmap_text, re.M)
    if m:
        body = m.group(1)
        if value is None:
            body = re.sub(r"^    iostandard:.*\n", "", body, flags=re.M)
        elif re.search(r"^    iostandard:", body, re.M):
            body = re.sub(r"^    iostandard:.*$", "    iostandard: {}".format(value), body, flags=re.M)
        else:
            body += "    iostandard: {}\n".format(value)
        if not body.strip():
            return pinmap_text[:m.start()] + pinmap_text[m.end():]
        return pinmap_text[:m.start()] + "  defaults:\n" + body + pinmap_text[m.end():]
    if value is None:
        return pinmap_text
    return re.sub(r"^(  pinBanks:)", "  defaults:\n    iostandard: {}\n\1".format(value), pinmap_text, count=1, flags=re.M)


def _render_io_overrides(entries):
    lines = ["  # Gowin IO_TYPE this BGM variant states beyond the board-wide ones (tools/sync_from_bgm.py --iotypes)",
             "  io_overrides:"]
    for ref, t in sorted(entries.items()):
        key = '"{}"'.format(ref) if any(c in ref for c in "[]") else ref
        lines.append("    {}: {}".format(key, t))
    return "\n".join(lines) + "\n"


def _set_config_io_overrides(text, entries):
    """Replace / insert / remove the `io_overrides:` block of a configuration."""
    text = re.sub(r"(?m)(?:^  # Gowin IO_TYPE this BGM variant[^\n]*\n)?^  io_overrides:\n(?:^ {4,}.*\n)*\n?", "", text)
    if not entries:
        return text
    block = _render_io_overrides(entries) + "\n"
    m = re.search(r"^  attach:", text, re.M)
    if not m:
        return text
    return text[:m.start()] + block + text[m.start():]


def apply_iotypes(paths, dry_run):
    """Gowin IO_TYPE exactly as BGM's CST files state it, per variant. For
    every pin of a board: the type goes into the pinmap (bank `iostandard`
    or per-pin `overrides`) when every BGM variant that locates the pin
    types it the same way; a type only some variants state goes into those
    configurations' `io_overrides:`. Types no variant states are removed:
    the tool's defaults and the bank voltages its embedded functions dictate
    are then what BGM gets too (an invented LVCMOS33 on the Tang Nano 9K's
    1.8 V bank 3 is refused with CT1136; a per-variant mix of typed and
    untyped pins in one bank is refused the same way)."""
    per_board = {}
    for path in paths:
        cfg = yaml.safe_load(open(path, encoding="utf-8"))["Configuration"]
        if cfg.get("toolchain") not in _GOWIN_TOOLCHAINS | _QUARTUS_TOOLCHAINS:
            continue
        vdir = bgm_oracle.variant_dir_for(cfg["id"], cfg["board"])
        if vdir is None:
            continue
        per_board.setdefault(cfg["board"], []).append(
            (path, cfg["id"], _bgm_iotypes(vdir), set(_bgm_signal_pins(vdir).values()),
             _bgm_qsf_default(vdir) if cfg.get("toolchain") in _QUARTUS_TOOLCHAINS else None))
    results = {}
    for board, variants in sorted(per_board.items()):
        pm_path = _pinmap_path(board)
        pinmap = config_init.read_board_pinmap(board) or {}
        banks = pinmap.get("pinBanks") or {}
        rev = _pin_to_ref(pinmap)

        # pin -> {type: set(configs)}, pin -> set(configs locating it)
        typed, located = {}, {}
        for path, cid, tmap, pins, _dflt in variants:
            for pin in pins:
                located.setdefault(pin, set()).add(cid)
            for pin, t in tmap.items():
                typed.setdefault(pin, {}).setdefault(t, set()).add(cid)
        conflicts = sorted(p for p, ts in typed.items() if len(ts) > 1)
        if conflicts:
            results[board] = "CONFLICT between BGM variants on pins {}".format(conflicts)
            continue
        bgm_defaults = {d for _p, _c, _t, _pins, d in variants}
        if len(bgm_defaults) > 1:
            results[board] = "CONFLICT between BGM variants on the project default I/O standard {}".format(sorted(bgm_defaults))
            continue
        bgm_default = next(iter(bgm_defaults))
        board_types, per_cfg = {}, {}          # pin -> type; cid -> {pin: type}
        for pin, ts in typed.items():
            (t, cids), = ts.items()
            if cids >= located.get(pin, set()):
                board_types[pin] = t
            else:
                for cid in cids:
                    per_cfg.setdefault(cid, {})[pin] = t

        # --- pinmap: bank-level type when every pin of the bank has it, else per-pin overrides
        text = original = open(pm_path, encoding="utf-8").read()
        defaults = pinmap.get("defaults") or {}
        keep_default = bool(defaults.get("iostandard_reason"))
        changes = []
        if keep_default:
            pass
        elif bgm_default:
            # BGM's `-to *`: the pinmap default; only pins typed differently need their own
            if defaults.get("iostandard") != bgm_default:
                text = _set_defaults_iostandard(text, bgm_default)
                changes.append("defaults.iostandard {} -> {} (BGM -to *)".format(defaults.get("iostandard"), bgm_default))
            board_types = {p: t for p, t in board_types.items() if t.upper() != bgm_default.upper()}
        else:
            text = _set_defaults_iostandard(text, None)
        for bank, b in banks.items():
            vals = _bank_pin_values(b)
            btypes = {v: board_types.get(_norm_pin(v)) for v in vals}
            uniform = vals and len(set(btypes.values())) == 1 and next(iter(btypes.values()))
            new_type = uniform or None
            new_over = {} if uniform else {v: t for v, t in btypes.items() if t}
            cur_type = (b or {}).get("iostandard")
            cur_over = {str(k): str(v) for k, v in ((b or {}).get("overrides") or {}).items()}
            if (new_type or "").upper() != (cur_type or "").upper():
                text = (_set_bank_attr(text, bank, "iostandard", new_type) if new_type
                        else _del_bank_attr(text, bank, "iostandard")) or text
                changes.append("{}: iostandard {} -> {}".format(bank, cur_type, new_type))
            if {k: v.upper() for k, v in new_over.items()} != {k: v.upper() for k, v in cur_over.items()}:
                text = _set_bank_overrides(text, bank, new_over) or text
                changes.append("{}: overrides {} -> {}".format(bank, cur_over, new_over))
        if defaults.get("iostandard") and not keep_default and not bgm_default:
            changes.append("defaults.iostandard removed (per-pin types instead)")
        if text != original:
            if not dry_run:
                open(pm_path, "w", encoding="utf-8").write(text)
                config_init.clear_cache()
        results[board] = "; ".join(changes) if changes else "pinmap already"

        # --- configurations: io_overrides for the variant-specific types
        for path, cid, _tmap, _pins, _dflt in variants:
            entries = {}
            mine = per_cfg.get(cid, {})
            # a pin shared by two banks (Tang Nano 9K: LCD colour pins and
            # TMDS pairs) belongs to the bank this configuration binds
            try:
                bound_cfg = yaml.safe_load(open(path, encoding="utf-8"))["Configuration"]
                bound = set()
                for a in bound_cfg.get("attach") or []:
                    for ref in (a.get("bind") or {}).values():
                        for one in (ref if isinstance(ref, list) else [ref]):
                            if isinstance(one, str):
                                bound.add(re.split(r"[.\[]", one.strip().strip('"'), 1)[0])
            except Exception:
                bound = set()
            rev_bound = _pin_to_ref({"pinBanks": {b: v for b, v in banks.items() if b in bound}})
            # collapse to bank / sub-key when the variant types every pin of it
            by_bank = {}
            for pin, t in mine.items():
                ref = rev_bound.get(pin) or rev.get(pin)
                if ref is None:
                    continue
                by_bank.setdefault(re.split(r"[.\[]", ref, 1)[0], {})[ref] = t
            for bank, refs in by_bank.items():
                vals = _bank_pin_values(banks.get(bank) or {})
                all_pins = {_norm_pin(v) for v in vals}
                if all_pins and all_pins <= set(mine) and len({mine[p] for p in all_pins}) == 1:
                    entries[bank] = mine[next(iter(all_pins))]
                else:
                    entries.update(refs)
            ctext = open(path, encoding="utf-8").read()
            cur = yaml.safe_load(ctext)["Configuration"].get("io_overrides") or {}
            if {str(k): str(v) for k, v in cur.items()} == entries:
                continue
            new_text = _set_config_io_overrides(ctext, entries)
            if new_text != ctext and not dry_run:
                open(path, "w", encoding="utf-8").write(new_text)
                config_init.clear_cache()
            results[cid] = "io_overrides = {}".format(entries) if entries else "io_overrides removed"
    return results


def _bank_pin_values(bank):
    pins = (bank or {}).get("pins")
    if isinstance(pins, str):
        return [pins]
    if isinstance(pins, list):
        return [v for v in pins if v is not None]
    if isinstance(pins, dict):
        out = []
        for v in pins.values():
            out.extend([x for x in (v if isinstance(v, list) else [v]) if x is not None])
        return out
    return []


def apply_gowin_options(paths, dry_run):
    per_board = {}
    for path in paths:
        cfg = yaml.safe_load(open(path, encoding="utf-8"))["Configuration"]
        if cfg.get("toolchain") not in _GOWIN_TOOLCHAINS:
            continue
        vdir = bgm_oracle.variant_dir_for(cfg["id"], cfg["board"])
        if vdir is None:
            continue
        opts, dev = bgm_oracle.gowin_options(vdir)
        if dev is None and not opts:
            continue            # yosys variants have no .tcl
        per_board.setdefault(cfg["board"], set()).add((dev, tuple(opts)))
    results = {}
    for board, variants in sorted(per_board.items()):
        if len(variants) > 1:
            results[board] = "CONFLICT between BGM variants: {}".format(sorted(variants))
            continue
        (dev, opts), = variants
        pm_path = _pinmap_path(board)
        pinmap = config_init.read_board_pinmap(board) or {}
        cur = (pinmap.get("toolchain_options") or {}).get("gowin") or {}
        want_opts = [o.lstrip("-") for o in opts]
        if cur.get("set_device_reason"):
            dev = cur.get("set_device")          # documented deviation from BGM's tcl (Tang Mega 138K)
        if cur.get("set_device") == dev and list(cur.get("options") or []) == want_opts:
            results[board] = "already"
            continue
        text = open(pm_path, encoding="utf-8").read()
        reason = cur.get("set_device_reason")
        gowin_lines = _render_gowin_block(dev, opts, reason).split("\n")
        gowin_sub = "\n".join(l for l in gowin_lines if l.startswith("    ")) + "\n"
        if re.search(r"^  toolchain_options:", text, re.M):
            # replace only the gowin: sub-block; apicula: and others stay
            text, n = re.subn(r"(?m)^    gowin:\n(?:^      .*\n)*", gowin_sub.replace("\\", "\\\\"), text, count=1)
            if n != 1:
                text = re.sub(r"^(  toolchain_options:\n)", r"\1" + gowin_sub.replace("\\", "\\\\"), text, count=1, flags=re.M)
            new = text
        else:
            block = "\n".join(gowin_lines) + "\n"
            new, n = re.subn(r"^(  pinBanks:)", block.replace("\\", "\\\\") + r"\1", text, count=1, flags=re.M)
            if n != 1:
                results[board] = "WARNING: no pinBanks: line in {}".format(pm_path)
                continue
        if not dry_run:
            open(pm_path, "w", encoding="utf-8").write(new)
            config_init.clear_cache()
        results[board] = "set_device {!r}, options {}".format(dev, want_opts)
    return results


# ---------------------------------------------------------------------------
# --prune-missing-banks: drop attaches whose every bind names an absent bank
# ---------------------------------------------------------------------------

def apply_prune_missing_banks(path, dry_run):
    """Hand-written configurations (nexys4_ddr_default) attach peripherals on
    banks the pinmap never had (USB-HID, Ethernet, QSPI, ...). Strict codegen
    refuses them; BGM's top does not wire them either. Remove an attach when
    none of its binds can resolve to an existing bank."""
    from tools import codegen
    original = open(path, encoding="utf-8").read()
    cfg = yaml.safe_load(original)["Configuration"]
    pinmap = config_init.read_board_pinmap(cfg["board"]) or {}
    config_init._apply_pin_overrides(cfg["id"], cfg, pinmap)
    banks = pinmap.get("pinBanks") or {}
    lines = original.split("\n")
    removed = []
    for idx, a in reversed(list(enumerate(cfg.get("attach") or []))):
        bind = a.get("bind") or {}
        if not bind:
            continue
        refs = [one for ref in bind.values() for one in (ref if isinstance(ref, list) else [ref]) if isinstance(one, str)]
        parsed = [codegen._parse_bank_ref(r) for r in refs]
        if refs and all(p is not None and p[0] not in banks for p in parsed):
            blocks = _find_attach_blocks(lines, a["peripheral"])
            # pick the block whose text contains this attach's first bind value
            first = str(refs[0])
            for i, j, _ in reversed(blocks):
                if first in "\n".join(lines[i:j]):
                    del lines[i:j]
                    removed.append("{} on {}".format(a["peripheral"], sorted({p[0] for p in parsed})))
                    break
    if not removed:
        return "unchanged"
    text = "\n".join(lines)
    if not text.endswith("\n"):
        text += "\n"
    if not dry_run:
        open(path, "w", encoding="utf-8").write(text)
    return "removed attaches on banks the pinmap lacks: " + "; ".join(removed)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--reset", action="store_true", help="sync the reset policy")
    p.add_argument("--sv-binds", action="store_true",
                   help="bind TM1638 / INMP441 / Pmod MIC3 exactly as BGM's board_specific_top.sv instantiates them")
    p.add_argument("--vga", action="store_true",
                   help="set vga_4bit colour widths from the pinmap and BGM, rebind rgb-shaped banks")
    p.add_argument("--prune-optional", action="store_true",
                   help="drop binds of optional signals whose sub-key/pin does not exist")
    p.add_argument("--prune-missing-banks", action="store_true",
                   help="drop attaches whose binds all name banks absent from the pinmap")
    p.add_argument("--polarity", action="store_true",
                   help="write active:/mirror: bank attributes into the pinmaps from BGM's inversions")
    p.add_argument("--gowin-options", action="store_true",
                   help="write Gowin set_device args and set_option flags into the pinmaps from BGM's .tcl")
    p.add_argument("--clock", action="store_true",
                   help="write BGM clk_mhz into the pinmaps' clock banks; add/repair clock_input attaches")
    p.add_argument("--seven-seg", action="store_true",
                   help="rewrite the on-board 7-segment attach to match the pinmap shape")
    p.add_argument("--clock-tree", action="store_true",
                   help="lab_clock, PLL pixel-clock frequency and LCD bl/init from BGM's top + gowin_rpll.v")
    p.add_argument("--quartus-options", action="store_true",
                   help="pinmap toolchain_options.quartus.global_assignments from BGM's board_specific.qsf")
    p.add_argument("--yosys-options", action="store_true",
                   help="pinmap toolchain_options.yosys.synth_options from BGM's board_info.source_bash SYNTH_CMD")
    p.add_argument("--components", action="store_true",
                   help="i2s_audio_out / gpio providers / led_bank order and polarity / tie: / invented "
                        "attaches exactly as BGM's board_specific_top.sv (tools/sync_components.py)")
    p.add_argument("--iotypes", action="store_true",
                   help="Gowin IO_TYPE per pin from BGM's CST files into the pinmaps (defaults / bank / overrides)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--only", nargs="*")
    args = p.parse_args(argv)
    if not (args.reset or args.clock or args.seven_seg or args.vga or args.prune_optional
            or args.sv_binds or args.prune_missing_banks or args.polarity or args.gowin_options
            or args.clock_tree or args.iotypes or args.components or args.quartus_options or args.yosys_options):
        p.error("nothing to do: pass one or more of --reset --clock --seven-seg --vga "
                "--prune-optional --prune-missing-banks --sv-binds --polarity --gowin-options --clock-tree "
                "--iotypes --components")
    if not bgm_oracle.has_bgm():
        print("BGM checkout not found at {}".format(bgm_oracle.BGM_BOARDS), file=sys.stderr)
        return 2
    paths = [q for q in sorted(glob.glob(os.path.join(REPO, "config", "configurations", "*.yml")))
             if not args.only or os.path.splitext(os.path.basename(q))[0] in args.only]
    if args.clock:
        for name, result in sorted(apply_clock(paths, args.dry_run).items()):
            print("[clock] {:44s} {}".format(name, result))
    if args.polarity:
        for name, result in sorted(apply_polarity(paths, args.dry_run).items()):
            print("[pol]   {:44s} {}".format(name, result))
    if args.gowin_options:
        for name, result in sorted(apply_gowin_options(paths, args.dry_run).items()):
            print("[gowin] {:44s} {}".format(name, result))
    if args.quartus_options:
        for name, result in sorted(apply_quartus_options(paths, args.dry_run).items()):
            print("[qsf]   {:44s} {}".format(name, result))
    if args.yosys_options:
        for name, result in sorted(apply_yosys_options(paths, args.dry_run).items()):
            print("[yosys] {:44s} {}".format(name, result))
    if args.iotypes:
        for name, result in sorted(apply_iotypes(paths, args.dry_run).items()):
            print("[iotyp] {:44s} {}".format(name, result))
    for path in paths:
        cid = os.path.splitext(os.path.basename(path))[0]
        if args.reset:
            print("[reset] {:44s} {}".format(cid, apply_reset(path, args.dry_run)))
        if args.seven_seg:
            print("[7seg]  {:44s} {}".format(cid, apply_seven_seg(path, args.dry_run)))
        if args.sv_binds:
            print("[sv]    {:44s} {}".format(cid, apply_sv_binds(path, args.dry_run)))
        if args.vga:
            print("[vga]   {:44s} {}".format(cid, apply_vga(path, args.dry_run)))
        if args.prune_optional:
            print("[prune] {:44s} {}".format(cid, apply_prune_optional(path, args.dry_run)))
        if args.prune_missing_banks:
            print("[banks] {:44s} {}".format(cid, apply_prune_missing_banks(path, args.dry_run)))
        if args.clock_tree:
            print("[clk]   {:44s} {}".format(cid, apply_clock_tree(path, args.dry_run)))
        if args.components:
            from tools import sync_components
            print("[comp]  {:44s} {}".format(cid, sync_components.apply_components(path, args.dry_run)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

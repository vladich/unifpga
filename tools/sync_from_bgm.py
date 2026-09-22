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
    vdir = bgm_oracle.variant_dir(cid, cfg["board"])
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
        vdir = bgm_oracle.variant_dir(cid, cfg["board"])
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

def _seven_seg_attach(pinmap):
    """Render the attach block for onboard_7seg according to the pinmap shape,
    or None when the board has no onboard_7seg bank."""
    bank = ((pinmap or {}).get("pinBanks") or {}).get("onboard_7seg")
    pins = (bank or {}).get("pins")
    if not isinstance(pins, dict):
        return None
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


def apply_seven_seg(path, dry_run):
    original = open(path, encoding="utf-8").read()
    cfg = yaml.safe_load(original)["Configuration"]
    pinmap = config_init.read_board_pinmap(cfg["board"]) or {}
    block = _seven_seg_attach(pinmap)
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
    if text == original:
        return "unchanged"
    if not dry_run:
        open(path, "w", encoding="utf-8").write(text)
    return "rewrote 7-segment attach: " + block.split("\n")[0].strip()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--reset", action="store_true", help="sync the reset policy")
    p.add_argument("--clock", action="store_true",
                   help="write BGM clk_mhz into the pinmaps' clock banks; add/repair clock_input attaches")
    p.add_argument("--seven-seg", action="store_true",
                   help="rewrite the on-board 7-segment attach to match the pinmap shape")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--only", nargs="*")
    args = p.parse_args(argv)
    if not (args.reset or args.clock or args.seven_seg):
        p.error("nothing to do: pass --reset, --clock and/or --seven-seg")
    if not bgm_oracle.has_bgm():
        print("BGM checkout not found at {}".format(bgm_oracle.BGM_BOARDS), file=sys.stderr)
        return 2
    paths = [q for q in sorted(glob.glob(os.path.join(REPO, "config", "configurations", "*.yml")))
             if not args.only or os.path.splitext(os.path.basename(q))[0] in args.only]
    if args.clock:
        for name, result in sorted(apply_clock(paths, args.dry_run).items()):
            print("[clock] {:44s} {}".format(name, result))
    for path in paths:
        cid = os.path.splitext(os.path.basename(path))[0]
        if args.reset:
            print("[reset] {:44s} {}".format(cid, apply_reset(path, args.dry_run)))
        if args.seven_seg:
            print("[7seg]  {:44s} {}".format(cid, apply_seven_seg(path, args.dry_run)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

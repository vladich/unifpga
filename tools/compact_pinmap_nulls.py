#!/usr/bin/env python3
"""
Compact `null` gaps out of pinmap pin lists.

Imported constraint files number buses the way the vendor did (`LED[8:1]`,
`LCD_R[7:3]`, `ck_io0..13, 26..41`), and the curator kept the gaps as `null`
entries. Codegen sizes the top-level port by the list length, so every null
became an unconstrained port bit and shifted the capability's bits away from
their pins (omdazz_epm570 LEDs, Tang Nano 9K LCD colours). BGM's numbering is
cosmetic (1-based names) or a sub-range (the 5 MSBs of an 8-bit colour), so
the gaps carry no information a design can use.

This tool:
  1. rewrites every inline list containing null in config/boards/**/*.yml
     (except _raw/) without the nulls and records the original index of the
     first kept entry as `index_origin` (bank-level; a mapping for sub-keyed
     banks) so the parity tool can still translate BGM's indices;
  2. remaps `bank[idx]` / `bank.sub[idx]` binds in every configuration to the
     new indices, and refuses (exit 1) if a bind pointed at a removed null;
  3. is idempotent.

    /usr/bin/python3 tools/compact_pinmap_nulls.py --dry-run
    /usr/bin/python3 tools/compact_pinmap_nulls.py
"""

import argparse
import glob
import os
import re
import sys

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
BOARDS = os.path.join(REPO, "config", "boards")
CONFIGS = os.path.join(REPO, "config", "configurations")

_INLINE_LIST = re.compile(r"\[([^\[\]]*)\]")
_NULL = re.compile(r"^(null|~)$")


def _split_items(inner):
    return [x.strip() for x in inner.split(",")] if inner.strip() else []


def compact_list_text(list_text):
    """`["a", null, "b"]` -> (`["a", "b"]`, old_to_new {0:0, 2:1}, first_kept)
    or (None, None, None) when there is nothing to compact."""
    m = _INLINE_LIST.fullmatch(list_text.strip())
    if not m:
        return None, None, None
    items = _split_items(m.group(1))
    if not any(_NULL.match(x) for x in items):
        return None, None, None
    kept, mapping = [], {}
    for i, x in enumerate(items):
        if _NULL.match(x):
            continue
        mapping[i] = len(kept)
        kept.append(x)
    if not kept:
        return None, None, None
    return "[" + ", ".join(kept) + "]", mapping, min(mapping)


def compact_pinmap(path, dry_run):
    """Returns {(bank, sub_or_None): old_to_new} for the banks changed."""
    text = open(path, encoding="utf-8").read()
    lines = text.split("\n")
    changes = {}
    out = []
    bank = None
    pending_origin = {}          # for the current bank: {sub_or_None: first_kept}
    bank_indent = None
    bank_header_idx = None

    def flush_origin():
        nonlocal pending_origin, bank_header_idx
        if pending_origin and bank_header_idx is not None:
            hdr = out[bank_header_idx]
            if "{" in hdr:                                   # inline bank
                origin = pending_origin.get(None)
                if origin is not None and "index_origin" not in hdr:
                    out[bank_header_idx] = hdr.replace(" }", ", index_origin: {} }}".format(origin), 1)
            else:
                subs = {k: v for k, v in pending_origin.items() if k is not None}
                flat = pending_origin.get(None)
                ins = None
                if flat is not None:
                    ins = "      index_origin: {}".format(flat)
                elif subs:
                    ins = "      index_origin: {" + ", ".join("{}: {}".format(k, v) for k, v in subs.items()) + "}"
                if ins and not any(l.strip().startswith("index_origin:") for l in out[bank_header_idx + 1:]):
                    out.insert(bank_header_idx + 1, ins)
        pending_origin = {}
        bank_header_idx = None

    for line in lines:
        m_bank = re.match(r"^(    )([A-Za-z_][A-Za-z0-9_]*):(.*)$", line)
        if m_bank and not line.startswith("      "):
            flush_origin()
            bank = m_bank.group(2)
            bank_header_idx = len(out)
            rest = m_bank.group(3)
            if "{" in rest:
                mm = re.search(r"pins:\s*(\[[^\]]*\])", rest)
                if mm:
                    new, mapping, origin = compact_list_text(mm.group(1))
                    if new:
                        line = line.replace(mm.group(1), new, 1)
                        changes[(bank, None)] = mapping
                        pending_origin[None] = origin
            out.append(line)
            continue
        m_pins = re.match(r"^(      pins:\s*)(\[[^\]]*\])\s*$", line)
        m_sub = re.match(r"^(        ([A-Za-z_][A-Za-z0-9_]*):\s*)(\[[^\]]*\])\s*$", line)
        if m_pins:
            new, mapping, origin = compact_list_text(m_pins.group(2))
            if new:
                line = m_pins.group(1) + new
                changes[(bank, None)] = mapping
                pending_origin[None] = origin
        elif m_sub:
            new, mapping, origin = compact_list_text(m_sub.group(3))
            if new:
                line = m_sub.group(1) + new
                changes[(bank, m_sub.group(2))] = mapping
                pending_origin[m_sub.group(2)] = origin
        out.append(line)
    flush_origin()
    new_text = "\n".join(out)
    if changes and not dry_run and new_text != text:
        open(path, "w", encoding="utf-8").write(new_text)
    return changes


_BIND_IDX = re.compile(r"^(?P<q>[\"']?)(?P<bank>[A-Za-z_][A-Za-z0-9_]*)(?:\.(?P<sub>[A-Za-z_][A-Za-z0-9_]*))?\[(?P<idx>\d+)\](?P=q)$")


def remap_configs(board_changes, dry_run):
    """board_changes: {board_id: {(bank, sub): old_to_new}}. Returns problems."""
    problems, edits = [], []
    for path in sorted(glob.glob(os.path.join(CONFIGS, "*.yml"))):
        text = open(path, encoding="utf-8").read()
        cfg = yaml.safe_load(text)["Configuration"]
        changes = board_changes.get(cfg["board"])
        if not changes:
            continue

        def fix(m):
            bank, sub, idx = m.group("bank"), m.group("sub"), int(m.group("idx"))
            mapping = changes.get((bank, sub))
            if mapping is None:
                return m.group(0)
            if idx not in mapping:
                problems.append("{}: bind {} points at a removed null entry".format(cfg["id"], m.group(0)))
                return m.group(0)
            new_idx = mapping[idx]
            q = m.group("q")
            return "{q}{b}{s}[{i}]{q}".format(q=q, b=bank, s=("." + sub) if sub else "", i=new_idx)

        def repl(m):
            bm = _BIND_IDX.match(m.group(0))
            return fix(bm) if bm else m.group(0)

        new_text = re.sub(r"[\"']?[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?\[\d+\][\"']?",
                          repl, text)
        if new_text != text:
            edits.append(cfg["id"])
            if not dry_run:
                open(path, "w", encoding="utf-8").write(new_text)
    return problems, edits


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    board_changes = {}
    for path in sorted(glob.glob(os.path.join(BOARDS, "*", "*", "*.yml"))):
        if os.sep + "_raw" + os.sep in path:
            continue
        changes = compact_pinmap(path, args.dry_run)
        if changes:
            board_id = os.path.splitext(os.path.basename(path))[0]
            board_changes[board_id] = changes
            for (bank, sub), mapping in sorted(changes.items()):
                print("{:28s} {}{}: {} -> {} entries (first kept index {})".format(
                    board_id, bank, "." + sub if sub else "", max(mapping) + 1, len(mapping), min(mapping)))
    problems, edits = remap_configs(board_changes, args.dry_run)
    for e in edits:
        print("config remapped: {}".format(e))
    for pr in problems:
        print("ERROR " + pr)
    print("{} pinmaps compacted, {} configurations remapped{}".format(
        len(board_changes), len(edits), " (dry run)" if args.dry_run else ""))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

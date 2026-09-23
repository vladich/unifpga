#!/usr/bin/env python3
"""
Per-configuration issue matrix.

For every config/configurations/<id>.yml this tool resolves the configuration,
runs the codegen, and checks it against (a) the board pinmap and (b) the
upstream basics-graphics-music (BGM) board directory that acts as the oracle.
It prints one row per configuration with the issue codes that apply, plus a
legend and per-code counts. It is the burn-down tracker for PLAN.md: a
configuration is "clean" when it has no codes left.

Issue codes (see PLAN.md, section "Issue catalogue"):

  SEG-BIND   7-segment binds reference sub-keys the pinmap does not have
  UNDECL     generated top references identifiers no port declares
  UNBOUND    a non-optional peripheral signal is not bound (driver port dangles)
  CLK-NONE   no clock provider at all
  CLK-FREQ   clock provider has no frequency_mhz (codegen defaults to 50)
  CLK-BGM    clk_mhz differs from BGM's board_specific_top.sv
  RST-NONE   generated top would tie rst to 0 (guard; cannot happen since P1.5)
  RST-BGM    configured reset policy differs from BGM's board_specific_top.sv
  PART       multi-chip board and the configuration does not pick a part
  PART-ID    chip Id is an ordering code passed verbatim to the tool
  DUP-PIN    the same physical pin is constrained for two ports
  NULL-PIN   a referenced bank has null entries (unconstrained port bits)
  WIDTH      params.width differs from the bound bank's pin count
  NO-VARIANT `_no_<x>` variant still attaches <x>
  PLL        PLL-derived clock frequencies differ from BGM's (E7), compared in
             the pixel domain: every PLL output is divided by its integer
             ratio to the pixel clock (1, 2, 5, 10), so BGM's 126 MHz DVI_TX
             serial clock (5x DDR) and our 252 MHz (10x SDR dvi_top) both
             read 25.2 MHz; a real difference (32.4 vs 33 MHz) still shows
  LAB-CLK    the lab (design_top, tm1638, resets) runs on a different clock
             than in BGM (`localparam lab_mhz = pixel_mhz` -> `lab_clock:`)
  DISPLAY    BGM's screen_width x screen_height differ from the attached
             display peripheral's (BGM tang_nano_9k_lcd_480_272_*_yosys build 800x480)
  GOWIN-OPT  BGM sets Gowin set_option flags the driver does not emit
  PIN-SET    the physical pins our constraints use differ from the pins BGM's
             active top has constrained (E1/E2: a lost or invented component)
  IOTYPE     Gowin IO_TYPE per pin differs from BGM's CST (BGM states none on
             most boards; an invented LVCMOS33 on the Tang Nano 9K's 1.8 V
             bank 3 makes Gowin refuse the design, CT1136)
  SEG-HEX    per-digit (hexN) display bound as a shared display (no adapter)
  SEG-MAP    a shared seven-segment pin gets another abcdefgh/digit bit or
             polarity than in BGM's board_specific_top.sv (E5)
  RGB        rgb_led attached but design_top receives w_rgb_led = 0

Retired codes (fixed at the source; E5 is now covered by co-simulation):
  DIFF-PAIR  (P3.2: pairs are located through the P port, N halves declared;
              rtl/io/diff_obuf.sv drives them with the vendor buffer)
  SEG-ORDER  (P1.3: seven_segment_8digit_shared pin_assigns are per bit, a = abcdefgh[7])
  TM1638     (P1.3: tm1638_led_key bit-reverses abcdefgh into hgfedcba)
  GPIO-IN    (P1.6: design_top.gpio is a net concatenation of the header pins)
  HUB75      (P3.4: hub75e_led_matrix.yml matches the module; binds from BGM; the
              validator's direction check covers the oscillator-as-output case)
  PMOD-IDX, MIC3, NO-VARIANT(tm1638)  (superseded by SV-BIND-DIFF once
              tools/sync_from_bgm.py --sv-binds derives these binds from BGM)
  POLARITY   BGM inverts keys/LEDs/switches for this board, config has no active:
  SV-BIND-DIFF  TM1638 / INMP441 / MIC3 attachment or pins differ from BGM's
             board_specific_top.sv instantiation (compared by pin identity)
  PIN-PREFIX pinmap stores pins with a vendor prefix (PIN_x)
  HAND       hand-written configuration that is neither an open-flow twin of
             a generated one nor marked `manual: true` with a documented oracle
  TWIN-DIFF  an `_openxc7` / `_mistral` / `_oxide` twin differs from its base
             configuration beyond id and toolchain
  NO-ORACLE  BGM has no directory for this variant (needs another oracle)
  GEN-ERROR  codegen.validate_configuration() rejects the configuration, so
             synthesize.py refuses to build it until the data is fixed

Usage:
    /usr/bin/python3 tools/audit_configs.py                # markdown table
    /usr/bin/python3 tools/audit_configs.py --json out.json
    /usr/bin/python3 tools/audit_configs.py --only basys3 de10_lite
"""

import argparse
import glob
import json
import logging
import os
import re
import sys
from collections import Counter, OrderedDict, defaultdict

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
CONFIG_DIR = os.path.join(REPO, "config", "configurations")
sys.path.insert(0, REPO)

from config import init as config_init          # noqa: E402
from tools import codegen                       # noqa: E402
from tools import bgm_oracle                    # noqa: E402
from tools import sync_from_bgm                 # noqa: E402

BGM_DIR = bgm_oracle.BGM_DIR
BGM_BOARDS = bgm_oracle.BGM_BOARDS

_DISPLAY_PERIPHERALS = {"lcd_480_272", "lcd_800_480", "lcd_ml6485", "hdmi_tmds",
                        "dvi_12bit", "dvi_24bit", "dvi_pmod_ddr_24b"}
_SEVEN_SEG_PERIPHERALS = {"seven_segment_8digit_shared", "seven_segment_per_digit"}
_GOWIN_TOOLCHAINS = {"gowin_eda", "gowin_standard", "nextpnr_apicula"}
_ORDERING_CODE = re.compile(r"^[A-Z0-9]+-[0-9][A-Z0-9]*$")   # XC7A35T-2FGG484I


# ---------------------------------------------------------------------------
# BGM oracle helpers
# ---------------------------------------------------------------------------

def bgm_dir_for(cfg_id, board_id):
    """BGM variant directory: the id, the id without an open-flow twin suffix
    (`_openxc7`, `_mistral`, `_oxide`), then the board id."""
    return bgm_oracle.variant_dir_for(cfg_id, board_id)


_BOARD_VARIANT_DIRS = {}


def _bgm_variant_dirs_of_board(board_id):
    """BGM variant directories of every configuration on this board."""
    if board_id not in _BOARD_VARIANT_DIRS:
        dirs = []
        for path in sorted(glob.glob(os.path.join(REPO, "config", "configurations", "*.yml"))):
            try:
                c = yaml.safe_load(open(path, encoding="utf-8"))["Configuration"]
            except Exception:
                continue
            if c.get("board") != board_id:
                continue
            d = bgm_oracle.variant_dir_for(c["id"], board_id)
            if d and d not in dirs:
                dirs.append(d)
        _BOARD_VARIANT_DIRS[board_id] = dirs
    return _BOARD_VARIANT_DIRS[board_id]


_TWIN_SUFFIXES = ("_openxc7", "_mistral", "_oxide", "_nextpnr")


def _twin_base_id(cfg_id):
    for suf in _TWIN_SUFFIXES:
        if cfg_id.endswith(suf):
            return cfg_id[:-len(suf)]
    return None


def _twin_diff(cfg_id, base_id):
    """What differs between an open-flow twin and its base configuration
    besides id and toolchain (nothing should: the twin is the same board,
    peripherals, reset, clocks and pin data on another flow)."""
    def load(cid):
        with open(os.path.join(CONFIG_DIR, cid + ".yml"), encoding="utf-8") as f:
            c = yaml.safe_load(f)["Configuration"]
        c.pop("id", None)
        c.pop("toolchain", None)
        c.pop("description", None)
        c.pop("manual", None)          # a twin of a hand-written configuration is one too
        return c
    a, b = load(cfg_id), load(base_id)

    def canon(attaches):
        # order does not matter (the sync appends derived attaches at the end)
        return sorted((yaml.safe_dump(x, sort_keys=True) for x in attaches or []))

    diffs = []
    for key in sorted(set(a) | set(b)):
        if key == "attach":
            if canon(a.get(key)) != canon(b.get(key)):
                ids_a = sorted(x.get("peripheral") for x in a.get(key) or [])
                ids_b = sorted(x.get("peripheral") for x in b.get(key) or [])
                diffs.append("attach {} vs {}".format(ids_a, ids_b) if ids_a != ids_b else "attach params/binds")
        elif a.get(key) != b.get(key):
            diffs.append(key)
    return ", ".join(diffs)


def bgm_top_source(bgm_dir):
    """Return (preprocessed text, effective_dir) of the variant's
    board_specific_top.sv with the full-lab profile (tools/bgm_oracle.py)."""
    if bgm_dir is None:
        return "", None
    pp = bgm_oracle.preprocess_variant(bgm_dir)
    eff = os.path.dirname(pp.files[-1]) if pp.files else bgm_dir
    return pp.text, eff


bgm_clk_mhz = bgm_oracle.clk_mhz
bgm_reset_exprs = bgm_oracle.reset_exprs
bgm_pll_instances = bgm_oracle.pll_instances
bgm_polarity_hints = bgm_oracle.polarity_hints


def classify_bgm_reset(exprs):
    """BGM reset kinds folded onto the policy vocabulary used by the
    configuration (`key_0`/`key_msb` count as `key`)."""
    kinds = set()
    for k in bgm_oracle.classify_reset(exprs):
        if k in ("key_0", "key_msb", "any_key"):
            kinds.add("key")
        elif k == "tm_key_msb":
            kinds.add("tm_key")
        elif k == "switch_msb":
            kinds.add("switch")
        else:
            kinds.add(k)
    return kinds


def bgm_gowin_options(bgm_dir):
    if bgm_dir is None:
        return []
    opts, _device = bgm_oracle.gowin_options(bgm_dir)
    return opts


# ---------------------------------------------------------------------------
# Per-configuration analysis
# ---------------------------------------------------------------------------

def _declared_ports(resolved):
    referenced = codegen.collect_referenced_banks(resolved)
    _, ports = codegen.fpga_port_decls(resolved, referenced)
    return {name for name, _w, _d in ports}


def _iter_bind_refs(attach):
    for sig, ref in (attach.get("bind") or {}).items():
        for one in (ref if isinstance(ref, list) else [ref]):
            if isinstance(one, str):
                yield sig, one


def _pins_by_port(resolved):
    """Replicates the bank walk of the constraint emitters: physical pin ->
    list of generated port bit names."""
    pinmap = resolved["board_pinmap"]
    out = defaultdict(list)
    nulls = []
    for bank_name in codegen.collect_referenced_banks(resolved):
        bank = (pinmap.get("pinBanks") or {}).get(bank_name)
        if bank is None:
            continue
        pins = bank.get("pins")
        if isinstance(pins, str):
            out[str(pins)].append(bank_name)
        elif isinstance(pins, list):
            for i, p in enumerate(pins):
                if p is None:
                    nulls.append("{}[{}]".format(bank_name, i))
                else:
                    out[str(p)].append("{}[{}]".format(bank_name, i))
        elif isinstance(pins, dict):
            for sub, val in pins.items():
                if not codegen._sub_used(resolved, bank_name, sub):
                    continue
                pname = "{}_{}".format(bank_name, sub)
                if isinstance(val, list):
                    for i, p in enumerate(val):
                        if p is None:
                            nulls.append("{}[{}]".format(pname, i))
                        else:
                            out[str(p)].append("{}[{}]".format(pname, i))
                elif isinstance(val, str):
                    out[str(val)].append(pname)
    return out, nulls


def analyze(cfg_id, cfg_text):
    issues = OrderedDict()       # code -> detail string

    def add(code, detail=""):
        if code in issues:
            if detail and detail not in issues[code]:
                issues[code] += "; " + detail
        else:
            issues[code] = detail

    resolved = config_init.resolve_configuration(cfg_id)
    cfg = resolved["configuration"]
    board = resolved["board"]
    pinmap = resolved["board_pinmap"]
    toolchain_id = resolved["toolchain"]["Id"]
    attaches = resolved["peripherals"]
    attached_ids = [a["peripheral_id"] for a in attaches]

    # Provenance: generated, an open-flow twin of a generated configuration
    # (same data, other toolchain), or a documented hand-written one.
    twin_base = _twin_base_id(cfg_id)
    if "Generated by tools/generate_variants.py" not in cfg_text:
        if twin_base and os.path.exists(os.path.join(CONFIG_DIR, twin_base + ".yml")):
            pass                                    # judged by TWIN-DIFF below
        elif not cfg.get("manual") and bgm_dir_for(cfg_id, cfg["board"]) is None:
            add("HAND")                             # with an oracle it is judged like a generated one
    if twin_base and os.path.exists(os.path.join(CONFIG_DIR, twin_base + ".yml")):
        diff = _twin_diff(cfg_id, twin_base)
        if diff:
            add("TWIN-DIFF", "vs {}: {}".format(twin_base, diff))

    # ---- BGM oracle -------------------------------------------------------
    bgm_dir = bgm_dir_for(cfg_id, cfg["board"])
    top_text, eff_dir = bgm_top_source(bgm_dir)
    if bgm_dir is None:
        add("NO-ORACLE")

    # ---- bind resolution ----------------------------------------------------
    declared = _declared_ports(resolved)
    for attach in attaches:
        perif = attach["peripheral"]
        sig_defs = {s["name"]: s for s in perif.get("signals", [])}
        bound = set()
        for sig, ref in _iter_bind_refs(attach):
            bound.add(sig)
            parsed = codegen._parse_bank_ref(ref)
            if parsed is None:
                add("UNDECL", "{}: unparsable ref {!r}".format(perif["id"], ref))
                continue
            bank, sub, idx = parsed
            pin = codegen._bank_pin(pinmap, bank, sub, idx)
            port = re.sub(r"\[\d+\]$", "", codegen._bank_ref_to_port(ref))
            if pin is None or port not in declared:
                if perif["id"] in _SEVEN_SEG_PERIPHERALS:
                    add("SEG-BIND", "{} -> {}".format(sig, ref))
                else:
                    if ((pinmap.get("pinBanks") or {}).get(bank) or {}).get("virtual"):
                        pass        # virtual clock source (fireant's internal oscillator): no port by design
                    else:
                        add("UNDECL", "{}.{} -> {}".format(perif["id"], sig, ref))
        for sig, sdef in sig_defs.items():
            if sig not in bound and not sdef.get("optional"):
                add("UNBOUND", "{}.{}".format(perif["id"], sig))
        # width parameter vs bank pin count for the simple banks
        w = (attach.get("params") or {}).get("width")
        if w is not None:
            for sig, ref in _iter_bind_refs(attach):
                parsed = codegen._parse_bank_ref(ref)
                if parsed and parsed[2] is None:
                    bw = codegen._bank_width(pinmap, parsed[0], parsed[1])
                    if bw is not None and bw != w:
                        add("WIDTH", "{}.{}: width {} vs {} pins".format(perif["id"], sig, w, bw))

    # ---- clock (same resolver codegen uses) ------------------------------------
    clock = codegen.resolve_clock(resolved)
    if clock is None:
        add("CLK-NONE")
        cg_clk = None
    else:
        if clock["mhz"] is None:
            add("CLK-FREQ", "codegen assumes 50")
        cg_clk = codegen._clk_mhz_int(clock)
    bclk = bgm_clk_mhz(top_text)
    if bclk is not None and cg_clk is not None and abs(float(cg_clk) - bclk) > 0.01:
        add("CLK-BGM", "codegen {} vs BGM {:g}".format(cg_clk, bclk))

    # ---- reset ----------------------------------------------------------------
    # RST-NONE: the generated top ties rst to 0 (cannot happen since P1.5, kept
    # as a guard). RST-BGM: the configured policy differs from what BGM's
    # board_specific_top.sv derives (union over its ifdef branches until the
    # oracle module evaluates the active branch).
    sources = codegen.reset_sources(resolved)
    kinds = sorted({k for k, _ in sources})
    if not sources:
        add("RST-NONE")
    rst_bgm = bgm_reset_exprs(top_text)
    if rst_bgm:
        bgm_kinds = classify_bgm_reset(rst_bgm)
        declared = bool((cfg.get("reset") or {}).get("sources")) or any(k == "pin" for k in kinds)
        if bgm_kinds and (set(kinds) != bgm_kinds or not declared):
            add("RST-BGM", "config {} vs BGM {} ({})".format(
                "+".join(kinds), "+".join(sorted(bgm_kinds)), " | ".join(rst_bgm)))

    # gpio: bidirectional since P1.6 (GPIO-IN retired); claimed bits are
    # visible in the generated top as gpio_nc_<n>.

    # ---- part selection ---------------------------------------------------------
    if board.get("Parts") and not cfg.get("part"):
        add("PART", "defaults to {}".format(board["Parts"][0].get("Part")))
    part = board.get("Part") or (board.get("Parts") or [{}])[0].get("Part") or ""
    if toolchain_id in ("vivado", "ise", "nextpnr_openxc7") and _ORDERING_CODE.match(part):
        add("PART-ID", part)

    # ---- physical pins ------------------------------------------------------------
    pins, nulls = _pins_by_port(resolved)
    dups = {p: ports for p, ports in pins.items() if len(ports) > 1}
    if dups:
        add("DUP-PIN", ", ".join("{}:{}".format(p, "/".join(v)) for p, v in sorted(dups.items())[:4])
            + (" ..." if len(dups) > 4 else ""))
    if nulls:
        add("NULL-PIN", ", ".join(nulls[:4]) + (" ..." if len(nulls) > 4 else ""))
    if any(str(p).startswith("PIN_") for p in pins):
        add("PIN-PREFIX")

    # ---- variant-name contradictions ----------------------------------------------
    for tag, perip in (("_no_hdmi", "hdmi_tmds"), ("_no_dvi", "dvi_12bit"), ("_no_dvi", "dvi_24bit")):
        if tag in cfg_id and perip in attached_ids:
            add("NO-VARIANT", "{} attached in {} variant".format(perip, tag))

    # ---- driver peripherals vs BGM's instantiations (TM1638, INMP441, MIC3) --------
    # E1/E6 for the peripherals BGM wires in board_specific_top.sv: same set of
    # modules instantiated, same pins (compared by pin identity, so Pmod
    # numbering conventions cannot fool it). Replaces the old presence codes
    # PMOD-IDX / MIC3 / NO-VARIANT(tm1638).
    if bgm_dir is not None:
        derived = sync_from_bgm._bgm_driver_binds(bgm_dir, pinmap)
        have = {a["peripheral_id"]: (a.get("bind") or {}) for a in attaches
                if a["peripheral_id"] in sync_from_bgm._SV_PERIPHERALS}
        for pid in sorted(set(derived) | set(have)):
            if pid not in derived:
                add("SV-BIND-DIFF", "{} attached but BGM does not instantiate it".format(pid))
            elif pid not in have:
                if derived[pid][0]:
                    add("SV-BIND-DIFF", "BGM instantiates {} on {} but it is not attached".format(pid, derived[pid][0]))
            else:
                want, _notes = derived[pid]
                got = {k: str(v).strip('"') for k, v in have[pid].items()}
                diff = {k: (got.get(k), v) for k, v in want.items() if got.get(k) != v}
                if diff:
                    add("SV-BIND-DIFF", "{}: {}".format(pid, diff))

    # ---- PLL / clock tree (E7) and lab clock ----------------------------------
    if bgm_dir is not None:
        pp_files = bgm_oracle.preprocess_variant(bgm_dir).files
        bgm_outs, unmodelled = bgm_oracle.pll_outputs(bgm_dir, top_text, pp_files)
        pix_m = re.search(r"\bpixel_mhz\s*=\s*([0-9.]+)", top_text)
        bgm_pixel = float(pix_m.group(1)) if pix_m else None

        def pixel_domain(f, ref):
            """f divided by its integer ratio (1/2/5/10) to the pixel clock."""
            if ref:
                k = f / ref
                for cand in (1, 2, 5, 10):
                    if abs(k - cand) / cand < 0.05:
                        return round(f / cand, 4)
            return round(f, 4)

        # our pixel clock first: a BGM top without a pixel_mhz parameter
        # (marsohod3gw2) is normalised against it
        try:
            tree = codegen.plan_clock_tree(resolved)
            our_pixel = next((sol.f_out for n, _r, _v, sol in tree if n == "pixel"), None)
        except codegen.CodegenError:
            tree, our_pixel = None, None
        bgm_pixel_set = {pixel_domain(m, bgm_pixel or our_pixel) for _n, m, _v in bgm_outs}
        # Gowin's DVI_TX IP hides its serial PLL; when BGM feeds it the board
        # clock as pixel clock (Tang Nano 4K: `.I_rgb_clk(clk)`) that clock is
        # BGM's pixel-domain frequency.
        for inst in bgm_oracle.instantiations(top_text, "DVI_TX_Top"):
            if dict(inst["ports"]).get("I_rgb_clk", "").strip().lower() in ("clk", "clk_in") and bgm_clk_mhz(top_text):
                bgm_pixel_set.add(round(bgm_clk_mhz(top_text), 4))
        bgm_set = sorted(bgm_pixel_set)
        if tree is not None:
            our_set = sorted({pixel_domain(sol.f_out, our_pixel) for _n, _r, v, sol in tree if v != "alias"})
            our_note = ""
        else:
            try:
                codegen.plan_clock_tree(resolved)
                our_note = ""
            except codegen.CodegenError as exc:
                our_note = " ({})".format(str(exc).split(": ", 1)[-1][:90])
            our_set = []
        if unmodelled:
            add("PLL", "BGM {} not modelled by the oracle; ours {} MHz".format(sorted(set(unmodelled)), our_set))
        elif bgm_set != our_set:
            add("PLL", "BGM {} MHz vs ours {} MHz{}".format(bgm_set, our_set, our_note))
        want_lab = bgm_oracle.lab_clock_source(top_text)
        have_lab = cfg.get("lab_clock") or "board"
        if isinstance(have_lab, dict):
            have_lab = "pll"
        bgm_lab_mhz = bgm_oracle.lab_mhz(top_text)
        try:
            our_lab_mhz = codegen.lab_clock(resolved)["mhz"]
        except codegen.CodegenError:
            our_lab_mhz = None
        if want_lab != have_lab or (bgm_lab_mhz is not None and our_lab_mhz is not None
                                    and int(round(bgm_lab_mhz)) != int(round(our_lab_mhz))):
            add("LAB-CLK", "BGM lab on {} clock ({} MHz) vs ours on {} ({} MHz)".format(
                want_lab, bgm_lab_mhz, have_lab, our_lab_mhz))
    if toolchain_id in _GOWIN_TOOLCHAINS and bgm_dir is not None:
        opts, dev = bgm_oracle.gowin_options(bgm_dir)
        want = [o.lstrip("-") for o in opts]
        have = (pinmap.get("toolchain_options") or {}).get("gowin") or {}
        if have.get("set_device_reason"):
            dev = have.get("set_device")            # documented tool deviation (Tang Mega 138K)
        if dev is not None or want:
            if have.get("set_device") != dev or list(have.get("options") or []) != want:
                add("GOWIN-OPT", "pinmap {!r}/{} vs BGM {!r}/{}".format(
                    have.get("set_device"), list(have.get("options") or []), dev, want))

    # ---- Gowin IO_TYPE per pin (E8) -------------------------------------------------
    if toolchain_id in _GOWIN_TOOLCHAINS and bgm_dir is not None:
        # per variant: the pinmap carries what every BGM variant of the board
        # states, the configuration's io_overrides the rest (sync --iotypes)
        bgm_types = sync_from_bgm._bgm_iotypes(bgm_dir)
        port_pin = {}
        for pin, ports_here in pins.items():
            for port in ports_here:
                port_pin[port] = sync_from_bgm._norm_pin(str(pin))     # pairs keep their "P,N" key
        ours = {}
        for m in re.finditer(r'IO_PORT\s+"([^"]+)"\s+IO_TYPE=(\S+?);', codegen.emit_cst(resolved)):
            pin = port_pin.get(m.group(1))
            if pin:
                ours[pin] = m.group(2).upper()
        # a pair is judged by its pair key or its P pin (BGM writes either);
        # its N half has no IO_PORT of its own; the emulated-differential `D`
        # suffix codegen adds for ELVDS pairs counts as BGM's plain type
        pair_n_halves = set()
        for pin in port_pin.values():
            if "," in pin:
                pair_n_halves.add(pin.split(",", 1)[1])

        def bgm_type_of(pin):
            if "," in pin:
                return bgm_types.get(pin) or bgm_types.get(pin.split(",", 1)[0])
            return bgm_types.get(pin)

        defaults = pinmap.get("defaults") or {}
        tool_default = defaults.get("iostandard") if defaults.get("iostandard_reason") else None
        diffs = []
        for pin in sorted(set(port_pin.values())):
            if pin in pair_n_halves:
                continue
            mine, theirs = ours.get(pin), bgm_type_of(pin)
            if mine and "," in pin and mine.endswith("D") and theirs and not theirs.endswith("D"):
                mine = mine[:-1]
            if theirs is None and mine == tool_default:
                continue            # documented tool requirement (Tang Nano 20K)
            if mine != theirs:
                diffs.append("{}: ours {} vs BGM {}".format(pin, ours.get(pin), theirs))
        if diffs:
            add("IOTYPE", "{} pin(s), e.g. {}".format(len(diffs), "; ".join(diffs[:3])))

    # ---- Quartus IO_STANDARD per pin (E8): ours from the generated QSF vs BGM's
    #      per-pin / wildcard / project-default assignments; a pin BGM leaves
    #      untyped gets the device default, so ours must state none either ----
    if toolchain_id in sync_from_bgm._QUARTUS_TOOLCHAINS and bgm_dir is not None:
        bgm_types = {k: v.upper() for k, v in sync_from_bgm._bgm_iotypes(bgm_dir).items()}
        # a pin BGM leaves untyped gets its project default (STRATIX_DEVICE_IO_STANDARD)
        bgm_default = (sync_from_bgm._bgm_qsf_default(bgm_dir) or "").upper() or None
        located = {sync_from_bgm._norm_pin(p) for p in sync_from_bgm._bgm_signal_pins(bgm_dir).values()}
        port_pin = {}
        for pin, ports_here in pins.items():
            for port in ports_here:
                port_pin[port] = sync_from_bgm._norm_pin(str(pin))
        try:
            qsf = codegen.emit_qsf(resolved, str(resolved["board"].get("Part") or "?"))
        except Exception:
            qsf = ""
        ours = {port_pin[m.group(2)]: m.group(1).upper() for m in
                re.finditer(r'IO_STANDARD\s+"([^"]+)"\s+-to\s+(\S+)', qsf) if m.group(2) in port_pin}
        om = re.search(r'STRATIX_DEVICE_IO_STANDARD\s+"([^"]+)"', qsf)
        our_default = om.group(1).upper() if om else None
        diffs = []
        for pin in sorted(set(port_pin.values()) & located):
            mine, theirs = ours.get(pin, our_default), bgm_types.get(pin, bgm_default)
            if mine != theirs:
                diffs.append("{}: ours {} vs BGM {}".format(pin, mine, theirs))
        if diffs:
            add("IOTYPE", "{} pin(s), e.g. {}".format(len(diffs), "; ".join(diffs[:3])))

    # ---- shared seven-segment bit map (E5): which abcdefgh / digit bit drives
    #      each physical pin, and with which polarity, vs BGM's assigns ---------
    if bgm_dir is not None and "seven_segment_8digit_shared" in attached_ids:
        sig_pins = sync_from_bgm._bgm_signal_pins(bgm_dir)
        bgm_map = bgm_oracle.expand_seven_seg_map(bgm_oracle.seven_seg_map(top_text), sig_pins)
        bgm_by_pin = {sig_pins[k]: v for k, v in bgm_map.items() if k in sig_pins}
        if bgm_by_pin:
            generated_top = codegen.emit_top_sv(resolved, strict=False)
            ours_by_port = {}
            for m in re.finditer(r"assign\s+(\{[^}]*\}|[A-Za-z_]\w*(?:\[\d+\])?)\s*=\s*(~?)\s*\(?\s*cap_seven_segment_(abcdefgh|digit)(?:\[(\d+)\])?\s*\)?\s*;",
                                 generated_top):
                lhs, inv, src, bit = m.group(1), bool(m.group(2)), m.group(3), m.group(4)
                kind = "seg" if src == "abcdefgh" else "dig"
                if lhs.startswith("{"):     # list bind: {p[n-1], ..., p[0]} = bus (MSB first)
                    names = [x.strip() for x in lhs[1:-1].split(",")]
                    for i, name in enumerate(reversed(names)):
                        ours_by_port[name] = (kind, i, inv)
                elif bit is not None:
                    ours_by_port[lhs] = (kind, int(bit), inv)
                else:                       # whole bus: port[i] = src[i]
                    for pin, ports_here in pins.items():
                        for pp in ports_here:
                            pm = re.match(r"^{}\[(\d+)\]$".format(re.escape(lhs)), pp)
                            if pm:
                                ours_by_port[pp] = (kind, int(pm.group(1)), inv)
            ours_by_pin = {}
            for pin, ports_here in pins.items():
                for pp in ports_here:
                    if pp in ours_by_port:
                        ours_by_pin[sync_from_bgm._norm_pin(pin)] = ours_by_port[pp]
            diffs = []
            for pin in sorted(set(bgm_by_pin) | set(ours_by_pin)):
                theirs = bgm_by_pin.get(pin)
                if theirs and theirs[0] == "const" and ours_by_pin.get(pin) is None:
                    continue        # BGM ties the pin off (zeowaa_wo_dig_0 digit 0); we leave it unbound
                if theirs != ours_by_pin.get(pin):
                    diffs.append("{}: ours {} vs BGM {}".format(pin, ours_by_pin.get(pin), theirs))
            if diffs:
                add("SEG-MAP", "{} pin(s), e.g. {}".format(len(diffs), "; ".join(diffs[:3])))

    # ---- physical pin set (E1/E2): the pins BGM's active top constrains vs the
    #      pins our generated constraints use ------------------------------------
    if bgm_dir is not None:
        # E1/E2: BGM ports its active top uses (named in the body beyond the
        # port list) must be pins of ours; a declared-but-untouched BGM port
        # floats like an unconstrained pin and is not demanded. Every pin of
        # ours must be a BGM port (declared or used).
        head, _sep, body = bgm_oracle.strip_comments(top_text).partition(");")
        all_ports = bgm_oracle.top_ports(top_text)
        used_ports = {pt.upper() for pt in all_ports
                      if re.search(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(pt), body)}
        declared_ports = {pt.upper() for pt in all_ports}
        bgm_used, bgm_declared = set(), set()
        for key, pin in sync_from_bgm._bgm_signal_pins(bgm_dir).items():
            base = key.split("[", 1)[0]
            halves = {sync_from_bgm._norm_pin(h) for h in str(pin).split(",")}
            if base in declared_ports:
                bgm_declared |= halves
            if base in used_ports:
                bgm_used |= halves
        our_pins = set()
        for pin in pins:
            for half in str(pin).split(","):
                our_pins.add(sync_from_bgm._norm_pin(half))
        missing = sorted(bgm_used - our_pins)
        extra = sorted(our_pins - bgm_declared)
        # tolerated on our side: the N half of a differential pair whose P pin
        # BGM constrains (Vivado places the partner itself: a7_lite TMDS), and
        # bits of a list bank no bind references (the vector port is declared
        # whole; a dangling bit is a tri-stated pin like an unconstrained one)
        pair_n = set()
        for pin in pins:
            if "," in str(pin):
                p_half, n_half = (sync_from_bgm._norm_pin(h) for h in str(pin).split(",", 1))
                if p_half in bgm_declared:
                    pair_n.add(n_half)
        referenced_bits = set()
        for a in attaches:
            for ref in (a.get("bind") or {}).values():
                referenced_bits.update(codegen._bind_bit_ports(resolved, ref))
        for src in (cfg.get("reset") or {}).get("sources") or []:
            if isinstance(src, dict) and src.get("pin"):
                referenced_bits.update(codegen._bind_bit_ports(resolved, str(src["pin"])))
        dangling = {sync_from_bgm._norm_pin(str(pin)) for pin, ports_here in pins.items()
                    if not any(pt in referenced_bits for pt in ports_here)}
        extra = [p for p in extra if p not in pair_n and p not in dangling]
        if missing or extra:
            rev = sync_from_bgm._pin_to_ref(pinmap)
            def _named(lst):
                return ", ".join("{} ({})".format(p, rev.get(p, "not in pinmap")) for p in lst[:6]) + (" ..." if len(lst) > 6 else "")
            add("PIN-SET", "BGM-only [{}]; ours-only [{}]".format(_named(missing), _named(extra)))

    # ---- peripheral-class issues ----------------------------------------------------
    if any(pid in _SEVEN_SEG_PERIPHERALS for pid in attached_ids):
        seg_bank = ((pinmap.get("pinBanks") or {}).get("onboard_7seg") or {}).get("pins")
        if (isinstance(seg_bank, dict) and any(k.startswith("hex") for k in seg_bank)
                and "seven_segment_per_digit" not in attached_ids):
            add("SEG-HEX", "pinmap has {}".format(", ".join(k for k in seg_bank if k.startswith("hex"))))
    if "rgb_led" in attached_ids:
        # Defect, not presence: the design must be told how many RGB LEDs exist.
        plans = codegen.build_capability_plans(resolved)
        if not plans["rgb_leds"].params.get("count"):
            add("RGB", "w_rgb_led would be 0")
        else:
            # NB: keep `top_text` for the BGM source; this is the generated top.
            generated_top = codegen.emit_top_sv(resolved, strict=False)
            if ".w_rgb_led(0)" in generated_top:
                add("RGB", "top passes w_rgb_led = 0")

    # ---- strict codegen verdict (what synthesize.py will do) ------------------
    problems = codegen.validate_configuration(resolved)
    if problems:
        add("GEN-ERROR", "{} problem(s); first: {}".format(
            len(problems), problems[0].split(": ", 1)[-1][:110]))
    if bgm_dir is not None and any(pid in _DISPLAY_PERIPHERALS for pid in attached_ids):
        size = bgm_oracle.screen_size(top_text)
        sp = codegen.build_capability_plans(resolved)["screen"].params
        ours = (sp.get("width"), sp.get("height"))
        if size is not None and size != ours:
            add("DISPLAY", "BGM screen {}x{} vs ours {}x{}".format(size[0], size[1], ours[0], ours[1]))

    # ---- polarity: effective (param > pinmap bank attribute > peripheral
    # default) vs what BGM's active branch does with the same pins -------------
    if bgm_dir is not None:
        derived = sync_from_bgm.derive_bank_polarity(bgm_dir, pinmap, cfg)
        for a in attaches:
            if a["peripheral_id"] not in sync_from_bgm._POLARITY_PERIPHERALS:
                continue
            banks_of_attach = {re.split(r"[.\[]", one, 1)[0]
                               for ref in (a.get("bind") or {}).values()
                               for one in (ref if isinstance(ref, list) else [ref]) if isinstance(one, str)}
            for bank in sorted(banks_of_attach):
                d = derived.get(bank)
                if d is None or d["active"] is None:
                    continue
                eff = codegen._peripheral_active_polarity(a["peripheral"], a, pinmap)
                eff_mirror = codegen._peripheral_mirror(a, pinmap)
                # an input bus's order (BGM's `SWAP_BITS (lab_key, ~ key_in)`, or a
                # `{ KEY2, KEY3, KEY4 }` concatenation) is placed bit by bit by
                # lab_bits, which the co-simulation proves; no mirror flag to compare
                mirror_matters = a["peripheral_id"] not in ("button_array", "sw_bank", "led_bank")
                if eff != d["active"] or (mirror_matters and bool(eff_mirror) != bool(d["mirror"])):
                    add("POLARITY", "{}.{}: unifpga {}{} vs BGM {}{} (ports {})".format(
                        a["peripheral_id"], bank, eff, " mirrored" if eff_mirror else "",
                        d["active"], " mirrored" if d["mirror"] else "", d["ports"]))

    return {
        "id": cfg_id,
        "board": cfg["board"],
        "toolchain": toolchain_id,
        "bgm_dir": os.path.basename(eff_dir) if eff_dir else None,
        "issues": issues,
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def render_markdown(rows):
    out = []
    out.append("| # | configuration | board | toolchain | BGM oracle | issues |")
    out.append("|---|---|---|---|---|---|")
    for i, r in enumerate(rows, 1):
        codes = " ".join("`{}`".format(c) for c in r["issues"]) or "clean"
        out.append("| {} | {} | {} | {} | {} | {} |".format(
            i, r["id"], r["board"], r["toolchain"], r["bgm_dir"] or "-", codes))
    out.append("")
    counts = Counter(c for r in rows for c in r["issues"])
    out.append("| code | configurations |")
    out.append("|---|---|")
    for code, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        out.append("| `{}` | {} |".format(code, n))
    out.append("")
    clean = [r["id"] for r in rows if not r["issues"]]
    out.append("Clean configurations: {} of {}".format(len(clean), len(rows)))
    return "\n".join(out)


def render_details(rows):
    out = []
    for r in rows:
        if not r["issues"]:
            continue
        out.append("### {}  ({} / {})".format(r["id"], r["board"], r["toolchain"]))
        for code, detail in r["issues"].items():
            out.append("- `{}`{}".format(code, (": " + detail) if detail else ""))
        # upstream bugs the sync found in BGM and did not reproduce: a finding
        # above may be unifpga doing the correct thing where BGM does not
        from tools import bgm_overlay
        for bug in ((bgm_overlay.load(r["id"]) or {}).get("bgm_bugs") or {}).values():
            out.append("- BGM bug, not reproduced: {}".format(bug))
        out.append("")
    return "\n".join(out)


def audit_all(only=None):
    """Analyze every configuration (or the `only` subset). Never raises: a
    configuration whose analysis crashes gets a single `ERROR` code so the
    matrix stays complete."""
    logging.disable(logging.CRITICAL)
    rows = []
    for path in sorted(glob.glob(os.path.join(REPO, "config", "configurations", "*.yml"))):
        cfg_id = os.path.splitext(os.path.basename(path))[0]
        if only and cfg_id not in only:
            continue
        text = open(path, encoding="utf-8").read()
        try:
            rows.append(analyze(cfg_id, text))
        except Exception as exc:              # keep the matrix complete
            rows.append({"id": cfg_id, "board": "?", "toolchain": "?", "bgm_dir": None,
                         "issues": OrderedDict([("ERROR", "{}: {}".format(type(exc).__name__, exc))])})
    return rows


BASELINE_PATH = os.path.join(REPO, "tests", "known_issues.yml")


def write_baseline(rows, path=BASELINE_PATH):
    """Persist {configuration_id: [codes]} as the accepted-debt baseline that
    tests/test_issue_gate.py ratchets against."""
    data = OrderedDict((r["id"], sorted(r["issues"])) for r in rows)
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Known per-configuration issue codes (see PLAN.md, tools/audit_configs.py).\n")
        f.write("# tests/test_issue_gate.py fails when a configuration gains a code that is\n")
        f.write("# not listed here, or when a listed code no longer applies (then the fix\n")
        f.write("# landed: regenerate with `tools/audit_configs.py --write-baseline`).\n")
        f.write("# Do not add codes by hand to silence the gate.\n")
        for cfg_id, codes in data.items():
            f.write("{}: [{}]\n".format(cfg_id, ", ".join(codes)))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", help="also write the raw result to this JSON file")
    p.add_argument("--details", action="store_true", help="append per-configuration detail sections")
    p.add_argument("--only", nargs="*", help="restrict to these configuration ids")
    p.add_argument("--write-baseline", action="store_true",
                   help="rewrite tests/known_issues.yml from the current result")
    args = p.parse_args(argv)

    rows = audit_all(args.only)
    if args.write_baseline:
        if args.only:
            p.error("--write-baseline needs the full matrix; drop --only")
        write_baseline(rows)
        print("Wrote {}".format(BASELINE_PATH), file=sys.stderr)
        return 0

    print(render_markdown(rows))
    if args.details:
        print()
        print(render_details(rows))
    if args.json:
        with open(args.json, "w") as f:
            json.dump(rows, f, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())

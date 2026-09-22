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
  PLL        PLL-derived clock frequencies differ from BGM's (E7): the set of
             frequencies BGM's gowin_rpll.v / SB_PLL40 settings produce vs the
             set codegen's clock tree (peripheral `clocks:`) instantiates
  LAB-CLK    the lab (design_top, tm1638, resets) runs on a different clock
             than in BGM (`localparam lab_mhz = pixel_mhz` -> `lab_clock:`)
  DISPLAY    BGM's screen_width x screen_height differ from the attached
             display peripheral's (BGM tang_nano_9k_lcd_480_272_*_yosys build 800x480)
  GOWIN-OPT  BGM sets Gowin set_option flags the driver does not emit
  DIFF-PAIR  a bound pin is a Gowin "P,N" pair the emitters skip
  SEG-HEX    per-digit (hexN) display bound as a shared display (no adapter)
  RGB        rgb_led attached but design_top receives w_rgb_led = 0

Retired codes (fixed at the source; E5 is now covered by co-simulation):
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
  HAND       hand-written configuration (not generated; must be re-derived)
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
sys.path.insert(0, REPO)

from config import init as config_init          # noqa: E402
from tools import codegen                       # noqa: E402
from tools import bgm_oracle                    # noqa: E402

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

    if "Generated by tools/generate_variants.py" not in cfg_text:
        add("HAND")

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
    if any("," in p for p in pins):
        add("DIFF-PAIR", ", ".join(sorted(p for p in pins if "," in p)[:3]))
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
        from tools import sync_from_bgm
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
        bgm_set = sorted({round(m, 4) for _n, m, _v in bgm_outs})
        try:
            our_set = sorted(round(sol.f_out, 4) for _n, _r, _v, sol in codegen.plan_clock_tree(resolved))
            our_note = ""
        except codegen.CodegenError as exc:
            our_set, our_note = [], " ({})".format(str(exc).split(": ", 1)[-1][:90])
        if unmodelled:
            add("PLL", "BGM {} not modelled by the oracle; ours {} MHz".format(sorted(set(unmodelled)), our_set))
        elif bgm_set != our_set:
            add("PLL", "BGM {} MHz vs ours {} MHz{}".format(bgm_set, our_set, our_note))
        want_lab = bgm_oracle.lab_clock_source(top_text)
        have_lab = cfg.get("lab_clock") or "board"
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
        if dev is not None or want:
            if have.get("set_device") != dev or list(have.get("options") or []) != want:
                add("GOWIN-OPT", "pinmap {!r}/{} vs BGM {!r}/{}".format(
                    have.get("set_device"), list(have.get("options") or []), dev, want))

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
        from tools import sync_from_bgm
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
                if eff != d["active"] or bool(eff_mirror) != bool(d["mirror"]):
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

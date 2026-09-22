"""
Shared SystemVerilog source-set collection for the toolchain drivers.

Every driver's `_collect_sv_sources()` used to be a near-identical private copy
of this walk; the copies drifted (only four of thirteen appended the rtl/pll
wrappers the generated top instantiates, so e.g. a7_lite_35t on openxc7 failed
with "Module pll_xilinx_mmcm ... is not part of the design"). The drivers now
delegate here and express their frontend quirks as flags:

    include_svh   Vivado / ISE read `.svh` headers as sources. Quartus, Gowin,
                  yosys & co. compile every listed file standalone, so headers
                  (pulled in by `include) must not be listed or their modules
                  get declared twice.
    gate_helpers  yosys 0.36 rejects SV-2009 multi-dim packed array ports
                  (tm1638_registers.sv), so the open flows add the
                  rtl/peripherals helpers only when the generated top names
                  the module. Vendor tools take them unconditionally.
    gate_common   Same idea for rtl/peripherals/designs_common/*.sv: include a
                  file only when its module name (the file stem) appears in the
                  generated top or any file collected so far.
    compat_stubs  rtl/peripherals/_quartus_compat/*.sv — pass-through BUFG /
                  IBUFG / BUFGCE stubs for designs that instantiate Xilinx
                  primitives directly. Flows that provide those natively
                  (Vivado unisim, synth_xilinx, synth_gowin, ...) must not get
                  them or the module is redefined.

The clock-tree wrappers and drivers' extra `files:` from
`codegen.pll_source_paths()` are always appended: they are what the generated
top instantiates, independent of the frontend.
"""

import os

from tools import codegen

# rtl/peripherals helper -> module names whose mention in the generated top
# means the helper is needed (the TM1638 file is used through two names).
HELPER_MODULES = {
    "tm1638_registers.sv":          ("tm1638_registers", "tm1638_board_controller"),
    "slow_clk_gen.sv":              ("slow_clk_gen",),
    "imitate_reset_on_power_up.sv": ("imitate_reset_on_power_up",),
}

# Files in the design directory that are never sources: the user top is added
# explicitly (first, by its given path) and the testbench is simulation-only.
SKIP_DESIGN_FILES = ("design_top.sv", "tb.sv")

DESIGNS_COMMON_DIR = os.path.join("rtl", "peripherals", "designs_common")
COMPAT_STUBS_DIR = os.path.join("rtl", "peripherals", "_quartus_compat")


def _read_text(path):
    try:
        with open(path) as fh:
            return fh.read()
    except Exception:
        return ""


def _sv_files_in(directory):
    """Sorted absolute `.sv` paths directly inside `directory` ([] if absent)."""
    if not os.path.isdir(directory):
        return []
    return [os.path.join(directory, name)
            for name in sorted(os.listdir(directory)) if name.endswith(".sv")]


def collect_sources(repo, peripherals, user_design_top, generated_top, *,
                    include_svh=False, gate_helpers=True, gate_common=True,
                    compat_stubs=False):
    """Ordered, de-duplicated absolute source list for one synthesis run.

    Order: generated top, the user's design top, the design directory walked
    recursively (`.sv`/`.v`, plus `.svh` with include_svh; design_top.sv and
    tb.sv skipped), each attached peripheral's `driver.file`, the
    rtl/peripherals helpers, designs_common, the compat stubs, and finally the
    clock-tree / `driver.files` sources from `codegen.pll_source_paths()`.
    """
    files = [generated_top, os.path.abspath(user_design_top)]
    seen = {os.path.abspath(p) for p in files}

    def add(path):
        if path not in seen:
            files.append(path)
            seen.add(path)

    exts = (".sv", ".svh", ".v") if include_svh else (".sv", ".v")
    design_dir = os.path.dirname(os.path.abspath(user_design_top))
    if os.path.isdir(design_dir):
        for root, _dirs, names in os.walk(design_dir):
            for name in sorted(names):
                if name.endswith(exts) and name not in SKIP_DESIGN_FILES:
                    add(os.path.join(root, name))

    for attach in peripherals or []:
        drv = (attach.get("peripheral") or {}).get("driver") or {}
        rel = drv.get("file")
        if rel:
            full = os.path.join(repo, rel)
            if os.path.exists(full):
                add(full)

    top_text = _read_text(generated_top) if (gate_helpers or gate_common) else ""

    for helper, modules in HELPER_MODULES.items():
        full = os.path.join(repo, "rtl", "peripherals", helper)
        if not os.path.exists(full):
            continue
        if gate_helpers and not any(m in top_text for m in modules):
            continue
        add(full)

    common = _sv_files_in(os.path.join(repo, DESIGNS_COMMON_DIR))
    if gate_common:
        sibling_text = top_text + "".join("\n" + _read_text(f) for f in list(files))
        common = [p for p in common
                  if os.path.basename(p)[:-len(".sv")] in sibling_text]
    for full in common:
        add(full)

    if compat_stubs:
        for full in _sv_files_in(os.path.join(repo, COMPAT_STUBS_DIR)):
            add(full)

    for full in codegen.pll_source_paths(repo, generated_top, peripherals):
        add(full)

    return files

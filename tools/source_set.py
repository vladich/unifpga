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
import shutil
import tempfile

import yaml

from tools import codegen

# rtl/peripherals helper -> module names whose mention in the generated top
# means the helper is needed (the TM1638 file is used through two names).
HELPER_MODULES = {
    "tm1638_registers.sv":          ("tm1638_registers", "tm1638_board_controller"),
    "slow_clk_gen.sv":              ("slow_clk_gen",),
    "imitate_reset_on_power_up.sv": ("imitate_reset_on_power_up",),
}

# The testbench is simulation-only. Explicit manifests place design_top.sv
# where package dependencies require it; legacy scans keep it first.
SKIP_DESIGN_DIRS = frozenset(("run", "build", "__pycache__", ".ater-tmp", ".git"))
DESIGN_FILESET = "fileset.yml"

DESIGNS_COMMON_DIR = os.path.join("rtl", "peripherals", "designs_common")
COMPAT_STUBS_DIR = os.path.join("rtl", "peripherals", "_quartus_compat")


class SourceSetError(ValueError):
    """A design's explicit source or asset list cannot be resolved safely."""


def _checked_files(design_dir, entries, field, extensions):
    if not isinstance(entries, list):
        raise SourceSetError("{}: {} must be a list".format(DESIGN_FILESET, field))
    paths = []
    seen = set()
    seen_real = set()
    root = os.path.realpath(design_dir)
    for rel in entries:
        if (not isinstance(rel, str) or not rel or "\\" in rel or ":" in rel or
                os.path.isabs(rel) or any(part in ("", ".", "..") for part in rel.split("/"))):
            raise SourceSetError("{}: invalid {} path {!r}".format(DESIGN_FILESET, field, rel))
        if rel in seen:
            raise SourceSetError("{}: duplicate {} path {!r}".format(DESIGN_FILESET, field, rel))
        seen.add(rel)
        path = os.path.join(design_dir, *rel.split("/"))
        real_path = os.path.realpath(path)
        if os.path.commonpath((root, real_path)) != root or not os.path.isfile(path):
            raise SourceSetError("{}: {} path is missing or escapes the design: {!r}"
                                 .format(DESIGN_FILESET, field, rel))
        if real_path in seen_real:
            raise SourceSetError("{}: duplicate {} file via {!r}"
                                 .format(DESIGN_FILESET, field, rel))
        seen_real.add(real_path)
        if not rel.endswith(extensions):
            raise SourceSetError("{}: unsupported {} file {!r}".format(DESIGN_FILESET, field, rel))
        paths.append(os.path.abspath(path))
    return paths


def design_inputs(design_dir):
    """Return ordered (sources, simulation-only sources, assets) for a design.

    Explicit fileset.yml selects alternative implementations. Older designs use
    a deterministic recursive scan, excluding generated build directories.
    Paths in a fileset are relative to the design, never to the caller's cwd.
    """
    design_dir = os.path.abspath(design_dir)
    manifest = os.path.join(design_dir, DESIGN_FILESET)
    if os.path.lexists(manifest):
        if os.path.islink(manifest):
            raise SourceSetError("{}: manifest must not be a symlink".format(manifest))
        try:
            with open(manifest, encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
        except (OSError, yaml.YAMLError) as exc:
            raise SourceSetError("{}: {}".format(manifest, exc)) from exc
        if not isinstance(data, dict) or set(data) != {"version", "sources", "simulation", "assets"}:
            raise SourceSetError("{}: expected version, sources, simulation, assets".format(manifest))
        if type(data["version"]) is not int or data["version"] != 1:
            raise SourceSetError("{}: unsupported version".format(manifest))
        sources = _checked_files(design_dir, data["sources"], "sources", (".sv", ".v", ".svh"))
        simulation = _checked_files(design_dir, data["simulation"], "simulation", (".sv", ".v"))
        assets = _checked_files(design_dir, data["assets"], "assets", (".hex", ".mem"))
        if len({os.path.realpath(p) for p in sources + simulation}) != len(sources) + len(simulation):
            raise SourceSetError("{}: source appears in both targets".format(manifest))
        if any(os.path.basename(p) == "tb.sv" for p in sources):
            raise SourceSetError("{}: tb.sv belongs in simulation".format(manifest))
        if sources.count(os.path.join(design_dir, "design_top.sv")) != 1:
            raise SourceSetError("{}: sources must include design_top.sv once".format(manifest))
        return sources, simulation, assets

    top = os.path.join(design_dir, "design_top.sv")
    sources = [top] if os.path.isfile(top) else []
    simulation, assets = [], []
    real_root = os.path.realpath(design_dir)
    for root, dirs, names in os.walk(design_dir):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DESIGN_DIRS)
        for name in sorted(names):
            path = os.path.join(root, name)
            if name.endswith((".sv", ".v", ".svh", ".hex", ".mem")):
                if os.path.commonpath((real_root, os.path.realpath(path))) != real_root:
                    raise SourceSetError("source or asset escapes the design: {!r}".format(path))
            if name == "tb.sv":
                if root == design_dir:
                    simulation.append(path)
            elif name == "design_top.sv":
                continue
            elif name.endswith((".sv", ".v", ".svh")):
                sources.append(path)
            elif name.endswith((".hex", ".mem")):
                assets.append(path)
    return sources, simulation, assets


def stage_assets(design_dir, output):
    """Copy declared ROM/data files to the tool's working directory."""
    _, _, assets = design_inputs(design_dir)
    output_root = os.path.realpath(output)
    for source in assets:
        rel = os.path.relpath(source, design_dir)
        target = os.path.join(output, rel)
        if (os.path.commonpath((output_root, os.path.realpath(target))) != output_root or
                os.path.islink(target)):
            raise SourceSetError("asset destination escapes output: {!r}".format(rel))
        temporary = None
        try:
            parent = os.path.dirname(target)
            os.makedirs(parent, exist_ok=True)
            if os.path.exists(target) and os.path.samefile(source, target):
                continue
            fd, temporary = tempfile.mkstemp(prefix=".unifpga-asset-", dir=parent)
            os.close(fd)
            shutil.copyfile(source, temporary)
            os.replace(temporary, target)
            temporary = None
        except OSError as exc:
            raise SourceSetError("could not stage asset {!r}: {}".format(rel, exc)) from exc
        finally:
            if temporary is not None and os.path.exists(temporary):
                os.unlink(temporary)
    return assets


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

    Order: generated top, the design's resolved sources (`.sv`/`.v`, plus
    `.svh` with include_svh), each attached peripheral's `driver.file`, the
    rtl/peripherals helpers, designs_common, the compat stubs, and finally the
    clock-tree / `driver.files` sources from `codegen.pll_source_paths()`.
    """
    design_dir = os.path.dirname(os.path.abspath(user_design_top))
    sources, _, _ = design_inputs(design_dir)
    manifest = os.path.join(design_dir, DESIGN_FILESET)
    user_top = os.path.abspath(user_design_top)
    if os.path.lexists(manifest):
        if user_top not in sources:
            raise SourceSetError("{}: selected top is not in sources: {!r}"
                                 .format(manifest, user_top))
        files = [generated_top]
    else:
        files = [generated_top, user_top]
        sources = [p for p in sources if p != os.path.join(design_dir, "design_top.sv")]
    seen = {os.path.abspath(p) for p in files}

    def add(path):
        if path not in seen:
            files.append(path)
            seen.add(path)

    exts = (".sv", ".svh", ".v") if include_svh else (".sv", ".v")
    for path in sources:
        if path.endswith(exts):
            add(path)

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

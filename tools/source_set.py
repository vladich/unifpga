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
                  rtl/peripherals helpers only when selected sources name
                  the module. Vendor tools take them unconditionally.
    gate_common   Same idea for rtl/peripherals/designs_common/*.sv: include a
                  file only when its module name (the file stem) appears in the
                  generated top or any file collected so far.

The clock-tree wrappers and drivers' extra `files:` from
`codegen.pll_source_paths()` are always appended: they are what the generated
top instantiates, independent of the frontend.
"""

import hashlib
import json
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
    "pdm_mic_decoder.sv":          ("pdm_mic_decoder",),
}

# The testbench is simulation-only. Explicit manifests place design_top.sv
# where package dependencies require it; legacy scans keep it first.
SKIP_DESIGN_DIRS = frozenset(("run", "build", "__pycache__", ".ater-tmp", ".git"))
DESIGN_FILESET = "fileset.yml"
COMPONENT_EXPORT_SCHEMA = "unifpga-component-export/v1"
MAX_COMPONENT_MANIFEST_BYTES = 1024 * 1024
MAX_COMPONENT_SOURCE_BYTES = 16 * 1024 * 1024

DESIGNS_COMMON_DIR = os.path.join("rtl", "peripherals", "designs_common")


class SourceSetError(ValueError):
    """A design's explicit source or asset list cannot be resolved safely."""


def _unique_json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key {!r}".format(key))
        result[key] = value
    return result


def component_export_sources(manifests, *, stage_dir=None):
    """Resolve digest-checked generated RTL without executing its generator.

    A component exporter owns generation and provenance. This source-set owner
    accepts only bounded, digest-checked RTL files. When stage_dir is set,
    checked bytes are copied into that private directory during hashing, so
    later compilation cannot reopen the mutable export. The manifest is never
    a command or a trust grant.
    """
    files = []
    seen = set()
    seen_components = set()
    total_bytes = 0
    for ordinal, manifest in enumerate(manifests):
        if ordinal >= 32:
            raise SourceSetError("too many component export manifests")
        manifest = os.path.abspath(os.fspath(manifest))
        if os.path.islink(manifest) or not os.path.isfile(manifest):
            raise SourceSetError("component export manifest is missing or a symlink: {!r}".format(manifest))
        try:
            with open(manifest, "rb") as fh:
                encoded = fh.read(MAX_COMPONENT_MANIFEST_BYTES + 1)
            if len(encoded) > MAX_COMPONENT_MANIFEST_BYTES:
                raise SourceSetError("component export manifest exceeds size limit: {!r}".format(manifest))
            report = json.loads(encoded.decode("utf-8"), object_pairs_hook=_unique_json_pairs)
        except (OSError, UnicodeError, ValueError, RecursionError) as exc:
            raise SourceSetError("invalid component export manifest {!r}: {}".format(manifest, exc)) from exc
        if not isinstance(report, dict) or report.get("schema") != COMPONENT_EXPORT_SCHEMA:
            raise SourceSetError("unsupported component export schema: {!r}".format(manifest))
        component = report.get("component")
        if not isinstance(component, str) or not component.strip() or component in seen_components:
            raise SourceSetError("invalid or duplicate component export identity: {!r}".format(manifest))
        seen_components.add(component)
        entries = report.get("files")
        if not isinstance(entries, list) or not 1 <= len(entries) <= 32:
            raise SourceSetError("component export needs 1..32 RTL files: {!r}".format(manifest))
        if stage_dir:
            try:
                manifest_copy = os.path.join(stage_dir, str(ordinal), "manifest.json")
                os.makedirs(os.path.dirname(manifest_copy), exist_ok=True)
                with open(manifest_copy, "xb") as fh:
                    fh.write(encoded)
            except OSError as exc:
                raise SourceSetError("cannot stage component export manifest: {}".format(exc)) from exc
        root = os.path.realpath(os.path.dirname(manifest))
        for entry in entries:
            if not isinstance(entry, dict):
                raise SourceSetError("invalid component export file record: {!r}".format(manifest))
            rel, size, digest = entry.get("path"), entry.get("size"), entry.get("sha256")
            parts = rel.split("/") if isinstance(rel, str) else []
            if (not isinstance(rel, str) or len(rel) > 1024 or
                    len(parts) > 32 or not rel.endswith((".sv", ".v")) or
                    "\\" in rel or ":" in rel or "\x00" in rel or os.path.isabs(rel) or
                    any(part in ("", ".", "..") for part in parts)):
                raise SourceSetError("invalid component export RTL path: {!r}".format(rel))
            if type(size) is not int or not 0 < size <= MAX_COMPONENT_SOURCE_BYTES:
                raise SourceSetError("invalid component export RTL size: {!r}".format(rel))
            if (not isinstance(digest, str) or len(digest) != 64 or
                    any(char not in "0123456789abcdef" for char in digest)):
                raise SourceSetError("invalid component export RTL digest: {!r}".format(rel))
            path = os.path.join(root, *parts)
            real = os.path.realpath(path)
            prefix = root
            symlinked = False
            for part in parts:
                prefix = os.path.join(prefix, part)
                if os.path.islink(prefix):
                    symlinked = True
                    break
            if (os.path.commonpath((root, real)) != root or
                    not os.path.isfile(path) or symlinked):
                raise SourceSetError("component export RTL is missing or escapes its root: {!r}".format(rel))
            if real in seen:
                raise SourceSetError("duplicate component export RTL file: {!r}".format(rel))
            seen.add(real)
            total_bytes += size
            if total_bytes > MAX_COMPONENT_SOURCE_BYTES:
                raise SourceSetError("component export RTL exceeds total size limit")
            actual = hashlib.sha256()
            actual_size = 0
            staged = os.path.join(stage_dir, str(ordinal), *parts) if stage_dir else None
            try:
                if staged:
                    os.makedirs(os.path.dirname(staged), exist_ok=True)
                with open(path, "rb") as fh:
                    target = open(staged, "xb") if staged else None
                    try:
                        for chunk in iter(lambda: fh.read(65536), b""):
                            actual_size += len(chunk)
                            if actual_size > size:
                                break
                            actual.update(chunk)
                            if target:
                                target.write(chunk)
                    finally:
                        if target:
                            target.close()
            except OSError as exc:
                raise SourceSetError("cannot read component export RTL {!r}: {}".format(rel, exc)) from exc
            if actual_size != size or actual.hexdigest() != digest:
                raise SourceSetError("component export RTL checksum mismatch: {!r}".format(rel))
            files.append(staged or path)
    return files


def stage_component_exports(manifests, output):
    """Publish one complete verified source snapshot inside a build output."""
    manifests = tuple(manifests)
    if not manifests:
        return []
    try:
        staging = tempfile.mkdtemp(prefix=".component-exports-", dir=output)
    except OSError as exc:
        raise SourceSetError("cannot stage component exports: {}".format(exc)) from exc
    try:
        sources = component_export_sources(manifests, stage_dir=staging)
        relative_sources = [os.path.relpath(source, staging) for source in sources]
        bundle_digest = hashlib.sha256()
        for ordinal in range(len(manifests)):
            with open(os.path.join(staging, str(ordinal), "manifest.json"), "rb") as fh:
                encoded = fh.read()
            bundle_digest.update(len(encoded).to_bytes(8, "big"))
            bundle_digest.update(encoded)
        published = os.path.join(output, "component-exports-" + bundle_digest.hexdigest())
        if os.path.lexists(published):
            if os.path.islink(published) or not os.path.isdir(published):
                raise SourceSetError("component export snapshot path is not a directory")
            prior = [os.path.join(published, str(i), "manifest.json")
                     for i in range(len(manifests))]
            existing = component_export_sources(prior)
            return existing
        os.rename(staging, published)
        staging = None
    except (OSError, SourceSetError) as exc:
        if isinstance(exc, SourceSetError):
            raise
        raise SourceSetError("cannot publish component exports: {}".format(exc)) from exc
    finally:
        if staging is not None:
            shutil.rmtree(staging)
    return [os.path.join(published, rel) for rel in relative_sources]


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
                    component_sources=()):
    """Ordered, de-duplicated absolute source list for one synthesis run.

    Order: generated top, verified component snapshots, the design's resolved
    sources (`.sv`/`.v`, plus
    `.svh` with include_svh), each attached peripheral's `driver.file`, the
    rtl/peripherals helpers, designs_common, and finally the
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
        legacy_top = None
    else:
        files = [generated_top]
        legacy_top = user_top
        sources = [p for p in sources if p != os.path.join(design_dir, "design_top.sv")]
    seen = {os.path.abspath(p) for p in files}

    def add(path):
        if path not in seen:
            files.append(path)
            seen.add(path)

    exts = (".sv", ".svh", ".v") if include_svh else (".sv", ".v")
    for path in component_sources:
        add(path)
    if legacy_top:
        add(legacy_top)
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

    # Design modules can instantiate reusable helpers directly; inspecting only
    # the generated wrapper misses those dependencies in prepared projects.
    source_text = ("".join("\n" + _read_text(f) for f in files)
                   if (gate_helpers or gate_common) else "")
    before_helpers = set(files)

    for helper, modules in HELPER_MODULES.items():
        full = os.path.join(repo, "rtl", "peripherals", helper)
        if not os.path.exists(full):
            continue
        if gate_helpers and not any(m in source_text for m in modules):
            continue
        add(full)

    common = _sv_files_in(os.path.join(repo, DESIGNS_COMMON_DIR))
    if gate_common:
        # to a fixed point: a common module another one instantiates
        # (pulse_extender -> shift_reg) is needed as soon as that one is
        text = source_text + "".join("\n" + _read_text(f) for f in files if f not in before_helpers)
        chosen, pending = [], list(common)
        grew = True
        while grew:
            grew = False
            for p in list(pending):
                if os.path.basename(p)[:-len(".sv")] in text:
                    chosen.append(p)
                    pending.remove(p)
                    text += "\n" + _read_text(p)
                    grew = True
        common = [p for p in common if p in chosen]
    for full in common:
        add(full)

    for full in codegen.pll_source_paths(repo, generated_top, peripherals):
        add(full)

    return files

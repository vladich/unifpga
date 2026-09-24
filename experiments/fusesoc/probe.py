"""Pinned, fixture-only CAPI2-to-EDAM setup probe for ECO-07."""

import argparse
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import subprocess
import sys

import yaml


CORE = "unifpga:probe:composed:1.0.0"
PULSE = "unifpga:probe:pulse:1.0.0"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
FILES = (
    (PULSE, "rtl/pulse.sv", "systemVerilogSource"),
    (CORE, "rtl/top.sv", "systemVerilogSource"),
    (CORE, "data/table.hex", "user"),
)


class ProbeError(ValueError):
    pass


def _digest(path):
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            size += len(chunk)
            if size > 4 * 1024 * 1024:
                raise ProbeError("fixture or export exceeds 4 MiB: " + str(path))
            digest.update(chunk)
    return digest.hexdigest()


def _contained(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ProbeError("EDAM contains an invalid relative path")
    parts = relative.replace("\\", "/").split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ProbeError("EDAM contains a traversal path")
    path = root.joinpath(*parts)
    if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise ProbeError("EDAM file is missing or escapes the work root: " + relative)
    return path


def run_probe(work_root, fixture_root=FIXTURES):
    """Resolve only trusted local fixture cores; never build or run a tool."""
    try:
        fusesoc_version = version("fusesoc")
        edalize_version = version("edalize")
    except PackageNotFoundError as exc:
        raise ProbeError("install the locked experiment dependencies") from exc
    if (fusesoc_version, edalize_version) != ("2.4.6", "0.6.7"):
        raise ProbeError("experiment dependency versions do not match pins")
    work_root = Path(work_root).resolve()
    fixture_root = Path(fixture_root).resolve()
    if work_root.exists() and any(work_root.iterdir()):
        raise ProbeError("work root must be empty")
    work_root.mkdir(parents=True, exist_ok=True)
    config = work_root / "fusesoc.conf"
    config.write_text("[main]\n", encoding="utf-8")
    build = work_root / "build"
    executable = str(Path(sys.executable).with_name("fusesoc"))
    command = [executable, "--config", str(config), "--cores-root",
               str(fixture_root), "run", "--setup", "--work-root", str(build), CORE]
    env = os.environ.copy()
    env["XDG_CACHE_HOME"] = str(work_root / "cache")
    try:
        process = subprocess.run(command, cwd=work_root, env=env, text=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProbeError("FuseSoC setup failed: " + str(exc)) from exc
    if process.returncode != 0:
        raise ProbeError("FuseSoC setup rejected fixture (exit {}): {}".format(
            process.returncode, process.stderr[-4000:].strip()))

    edam_paths = list(build.glob("*.eda.yml"))
    if len(edam_paths) != 1 or edam_paths[0].stat().st_size > 2 * 1024 * 1024:
        raise ProbeError("expected exactly one bounded EDAM document")
    try:
        edam = yaml.safe_load(edam_paths[0].read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ProbeError("could not decode generated EDAM") from exc
    if not isinstance(edam, dict):
        raise ProbeError("EDAM is not a mapping")
    if edam.get("toplevel") != "top" or edam.get("dependencies") != {
            PULSE: [], CORE: [PULSE]}:
        raise ProbeError("EDAM lost top or exact dependency closure")
    parameters = edam.get("parameters")
    if (not isinstance(parameters, dict) or
            not isinstance(parameters.get("WIDTH"), dict) or
            parameters["WIDTH"].get("default") != 4):
        raise ProbeError("EDAM lost WIDTH parameter")
    flow_options = edam.get("flow_options")
    if not isinstance(flow_options, dict) or flow_options.get("tool") != "icarus":
        raise ProbeError("EDAM lost Flow API tool selection")

    actual = edam.get("files")
    if not isinstance(actual, list) or len(actual) != len(FILES):
        raise ProbeError("EDAM lost or added files")
    resolved = []
    for entry, (core, source_name, file_type) in zip(actual, FILES):
        if not isinstance(entry, dict) or entry.get("core") != core or entry.get("file_type") != file_type:
            raise ProbeError("EDAM changed file order, ownership, or type")
        if entry.get("name") != "src/{}/{}".format(core.replace(":", "_"), source_name):
            raise ProbeError("EDAM changed the exported source path")
        exported = _contained(build, entry.get("name"))
        source = _contained(fixture_root, source_name)
        if _digest(exported) != _digest(source):
            raise ProbeError("exported file differs from core source: " + source_name)
        resolved.append({"core": core, "source": source_name, "type": file_type,
                         "sha256": _digest(source), "edam_path": entry["name"]})

    core_digests = {}
    edam_cores = edam.get("cores")
    if not isinstance(edam_cores, dict) or set(edam_cores) != {PULSE, CORE}:
        raise ProbeError("EDAM lost exact core identities")
    for core, name in ((PULSE, "pulse.core"), (CORE, "composed.core")):
        source_core = _contained(fixture_root, name)
        entry = edam_cores[core]
        if not isinstance(entry, dict) or not isinstance(entry.get("core_file"), str):
            raise ProbeError("EDAM changed core provenance")
        if (build / entry["core_file"]).resolve() != source_core.resolve():
            raise ProbeError("EDAM changed core provenance")
        core_digests[core] = _digest(source_core)
    makefile_path = _contained(build, "Makefile")
    if makefile_path.stat().st_size > 1024 * 1024:
        raise ProbeError("generated Makefile exceeds 1 MiB")
    makefile = makefile_path.read_text(encoding="utf-8")
    return {
        "schema": "unifpga-fusesoc-probe/v1",
        "fusesoc_version": fusesoc_version,
        "edalize_version": edalize_version,
        "edam_version": edam.get("version"),
        "core": CORE,
        "core_sha256": core_digests,
        "top": edam["toplevel"],
        "dependencies": edam["dependencies"],
        "files": resolved,
        "width_default": 4,
        "flow": "generic",
        "flow_tool": edam["flow_options"]["tool"],
        "asset_listed_in_makefile": actual[-1]["name"] in makefile,
        "tool_executed": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-root", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = run_probe(args.work_root)
    except ProbeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

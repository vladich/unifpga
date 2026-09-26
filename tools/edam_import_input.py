"""Admit a resolved EDAM as a source inventory for the Slang import frontend.

FuseSoC setup is a separate, trusted operation. This reader executes no core,
generator, hook, backend, or tool. It supports a deliberately small EDAM 0.2.1
subset and fails when a field could affect the source closure but is unknown.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile

import yaml

from config.init import _UniqueKeyLoader
from experiments.slang import probe
from tools import sv_import_candidate


SCHEMA = "unifpga.edam-import-input/v1"
CANDIDATE_SCHEMA = "unifpga.edam-import-candidate/v1"
MAX_EDAM_BYTES = 2 * 1024 * 1024
MAX_CANDIDATE_BYTES = 4 * 1024 * 1024
MAX_FILES = 256
MAX_TOTAL_BYTES = 32 * 1024 * 1024
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_EDAM_FIELDS = {"version", "name", "toplevel", "dependencies", "cores",
                "parameters", "filters", "flow_options", "hooks", "files", "vpi"}
_CORE_FIELDS = {"core_file", "dependencies", "license"}
_FILE_FIELDS = {"name", "file_type", "core", "is_include_file"}
_HDL_TYPES = {"systemVerilogSource", "verilogSource"}


class EdamImportError(ValueError):
    pass


def _read_edam(path):
    if path.is_symlink() or not path.is_file():
        raise EdamImportError("EDAM is missing or is a symlink")
    with path.open("rb") as stream:
        raw = stream.read(MAX_EDAM_BYTES + 1)
    if len(raw) > MAX_EDAM_BYTES:
        raise EdamImportError("EDAM exceeds 2 MiB")
    try:
        document = yaml.load(raw.decode("utf-8"), Loader=_UniqueKeyLoader)
    except (UnicodeError, yaml.YAMLError) as exc:
        raise EdamImportError("invalid EDAM YAML: {}".format(exc)) from exc
    if not isinstance(document, dict) or set(document) != _EDAM_FIELDS or \
            document["version"] != "0.2.1":
        raise EdamImportError("unsupported EDAM version or fields")
    if any(document[field] for field in ("filters", "hooks", "vpi")):
        raise EdamImportError("EDAM filters, hooks, or VPI inputs need separate admission")
    return document, hashlib.sha256(raw).hexdigest()


def _core_file(work_root, core_root, value):
    if not isinstance(value, str) or not value or len(value) > 1024 or \
            len(value.split("/")) > 64 or "\\" in value or ":" in value or \
            Path(value).is_absolute() or any(ord(ch) < 32 for ch in value):
        raise EdamImportError("invalid core_file path")
    cursor = work_root
    for part in value.split("/"):
        if part in ("", "."):
            raise EdamImportError("invalid core_file path")
        cursor = cursor / part
        if cursor.is_symlink():
            raise EdamImportError("symlink in core_file path")
    path = cursor.resolve()
    if not path.is_relative_to(core_root) or not path.is_file():
        raise EdamImportError("core_file is outside the admitted source root")
    return path


def _dependencies(document):
    cores, edges = document["cores"], document["dependencies"]
    if not isinstance(cores, dict) or not 0 < len(cores) <= MAX_FILES or \
            not isinstance(edges, dict) or set(cores) != set(edges):
        raise EdamImportError("EDAM core and dependency inventories differ")
    for core, dependencies in edges.items():
        if not isinstance(core, str) or not core or not isinstance(dependencies, list) or \
                len(dependencies) > MAX_FILES or \
                any(not isinstance(dep, str) or dep not in cores for dep in dependencies) or \
                len(set(dependencies)) != len(dependencies):
            raise EdamImportError("invalid EDAM dependency edge")
    marks = {}

    def visit(core):
        if marks.get(core) == 1:
            raise EdamImportError("EDAM dependency cycle")
        if marks.get(core) == 2:
            return
        marks[core] = 1
        for dependency in edges[core]:
            visit(dependency)
        marks[core] = 2

    for core in cores:
        visit(core)
    return cores, edges


def _overrides(parameters):
    if not isinstance(parameters, dict) or len(parameters) > MAX_FILES:
        raise EdamImportError("invalid EDAM parameter inventory")
    overrides = []
    for name, item in parameters.items():
        if not isinstance(name, str) or not _IDENTIFIER.fullmatch(name) or \
                not isinstance(item, dict) or item.get("paramtype") != "vlogparam" or \
                not set(item) <= {"paramtype", "datatype", "default", "description"} or \
                item.get("datatype") != "int" or type(item.get("default")) is not int:
            raise EdamImportError("unsupported EDAM parameter: {!r}".format(name))
        overrides.append("{}={}".format(name, item["default"]))
    return overrides


def import_edam(work_root, edam_path, core_root, compilation_unit):
    """Return a digest-checked inventory and an exact Slang v2 request."""
    work_root = Path(work_root).resolve(strict=True)
    core_root = Path(core_root).resolve(strict=True)
    edam_path = Path(edam_path)
    if not work_root.is_dir() or not core_root.is_dir() or \
            not edam_path.resolve().is_relative_to(work_root) or \
            compilation_unit not in ("separate", "single"):
        raise EdamImportError("invalid EDAM roots or compilation-unit policy")
    document, edam_digest = _read_edam(edam_path)
    cores, dependencies = _dependencies(document)
    if not isinstance(document["toplevel"], str) or \
            not _IDENTIFIER.fullmatch(document["toplevel"]) or \
            not isinstance(document["name"], str) or not document["name"] or \
            not isinstance(document["flow_options"], dict) or \
            set(document["flow_options"]) != {"tool"} or \
            not isinstance(document["flow_options"]["tool"], str) or \
            not document["flow_options"]["tool"]:
        raise EdamImportError("invalid EDAM top, name, or flow options")
    core_files = []
    total_bytes = 0
    for name, item in cores.items():
        if not isinstance(item, dict) or set(item) != _CORE_FIELDS or \
                item["dependencies"] != dependencies[name]:
            raise EdamImportError("EDAM core metadata differs from dependency graph")
        path = _core_file(work_root, core_root, item["core_file"])
        info = probe.digest_file(path)
        total_bytes += info["bytes"]
        if total_bytes > MAX_TOTAL_BYTES:
            raise EdamImportError("EDAM core and exported files exceed 32 MiB")
        core_files.append({"core": name, "path": path.relative_to(core_root).as_posix(),
                           **info, "license": item["license"]})
    entries = document["files"]
    if not isinstance(entries, list) or not 0 < len(entries) <= MAX_FILES:
        raise EdamImportError("invalid EDAM file inventory")
    files, sources, include_dirs = [], [], []
    seen, seen_include_dirs = set(), set()
    for item in entries:
        if not isinstance(item, dict) or not set(item) <= _FILE_FIELDS or \
                not {"name", "file_type", "core"} <= set(item) or \
                not isinstance(item["core"], str) or item["core"] not in cores or \
                type(item.get("is_include_file", False)) is not bool:
            raise EdamImportError("unsupported EDAM file record")
        file_type = item["file_type"]
        if not isinstance(file_type, str) or file_type not in _HDL_TYPES | {"user"}:
            raise EdamImportError("unsupported EDAM file type: {!r}".format(file_type))
        if file_type == "user" and item.get("is_include_file", False):
            raise EdamImportError("user asset cannot be a source include")
        try:
            path = probe.admit_path(work_root, item["name"], "source")
            info = probe.digest_file(path)
        except probe.ProbeError as exc:
            raise EdamImportError(str(exc)) from exc
        rel = path.relative_to(work_root).as_posix()
        if rel in seen:
            raise EdamImportError("duplicate EDAM exported file: " + rel)
        seen.add(rel)
        total_bytes += info["bytes"]
        if total_bytes > MAX_TOTAL_BYTES:
            raise EdamImportError("EDAM core and exported files exceed 32 MiB")
        kind = "asset" if file_type == "user" else \
               "include" if item.get("is_include_file", False) else "source"
        files.append({"path": rel, "core": item["core"], "file_type": file_type,
                      "kind": kind, **info})
        if kind == "source":
            sources.append(rel)
        elif kind == "include":
            parent = str(Path(rel).parent).replace("\\", "/")
            if parent not in seen_include_dirs:
                include_dirs.append(parent)
                seen_include_dirs.add(parent)
    if not sources:
        raise EdamImportError("EDAM has no supported HDL source")
    request = {"schema": probe.SCHEMA_V2, "sources": sources,
               "include_dirs": include_dirs, "defines": [],
               "top": document["toplevel"], "compilation_unit": compilation_unit,
               "top_parameter_overrides": _overrides(document["parameters"])}
    return {"schema": SCHEMA, "edam_version": document["version"],
            "edam_name": document["name"], "edam_sha256": edam_digest,
            "core_files": core_files, "dependencies": dependencies,
            "flow_options": document["flow_options"], "files": files,
            "slang_request": request, "asset_placement": "unverified"}


def verified_candidate(work_root, edam_path, core_root, compilation_unit, scratch_root):
    """Bind EDAM file digests to one Slang elaboration of the same fileset.

    This catches ordinary source churn, not a malicious concurrent writer;
    external repositories still need an immutable admitted worker snapshot.
    """
    work_root = Path(work_root).resolve(strict=True)
    core_root = Path(core_root).resolve(strict=True)
    scratch_root = Path(scratch_root)
    if scratch_root.is_symlink() or not scratch_root.is_dir():
        raise EdamImportError("scratch root must be an existing directory")
    bundle = import_edam(work_root, edam_path, core_root, compilation_unit)
    with tempfile.TemporaryDirectory(prefix="edam-import-", dir=scratch_root) as temporary:
        request_path = Path(temporary) / "request.json"
        request_path.write_text(json.dumps(bundle["slang_request"], sort_keys=True,
                                           separators=(",", ":")), encoding="utf-8")
        result = probe.run(work_root, request_path)
    declared_sources = {entry["path"]: (entry["bytes"], entry["sha256"])
                        for entry in bundle["files"] if entry["kind"] == "source"}
    declared_includes = {entry["path"]: (entry["bytes"], entry["sha256"])
                         for entry in bundle["files"] if entry["kind"] == "include"}
    observed = {entry["path"]: (entry["bytes"], entry["sha256"])
                for entry in result["read_files"]}
    if any(observed.get(path) != digest for path, digest in declared_sources.items()) or \
            any(path not in declared_sources and declared_includes.get(path) != digest
                for path, digest in observed.items()):
        raise EdamImportError("Slang read files differ from the EDAM HDL/include closure")
    for entry in bundle["files"]:
        info = probe.digest_file(probe.admit_path(work_root, entry["path"], "source"))
        if (info["bytes"], info["sha256"]) != (entry["bytes"], entry["sha256"]):
            raise EdamImportError("EDAM exported file changed during elaboration")
    for entry in bundle["core_files"]:
        info = probe.digest_file(probe.admit_path(core_root, entry["path"], "source"))
        if (info["bytes"], info["sha256"]) != (entry["bytes"], entry["sha256"]):
            raise EdamImportError("EDAM core file changed during elaboration")
    _, current_digest = _read_edam(Path(edam_path))
    if current_digest != bundle["edam_sha256"]:
        raise EdamImportError("EDAM changed during elaboration")
    try:
        rtl = sv_import_candidate.candidate_from_probe(result)
    except sv_import_candidate.CandidateError as exc:
        raise EdamImportError(str(exc)) from exc
    report = {"schema": CANDIDATE_SCHEMA, "edam_input": bundle,
              "rtl_candidate": rtl, "source_closure": "checked_before_and_after_elaboration",
              "unused_declared_includes": sorted(set(declared_includes) - set(observed))}
    report["candidate_sha256"] = hashlib.sha256(json.dumps(
        report, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
    if len(json.dumps(report, separators=(",", ":")).encode("utf-8")) > MAX_CANDIDATE_BYTES:
        raise EdamImportError("combined EDAM import candidate exceeds 4 MiB")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--edam", required=True, type=Path)
    parser.add_argument("--core-root", required=True, type=Path)
    parser.add_argument("--compilation-unit", required=True, choices=("separate", "single"))
    parser.add_argument("--candidate", action="store_true",
                        help="elaborate and verify the EDAM closure in one call")
    parser.add_argument("--scratch-root", type=Path,
                        help="task-owned temporary root required with --candidate")
    args = parser.parse_args(argv)
    if bool(args.scratch_root) != args.candidate:
        parser.error("--candidate and --scratch-root must be supplied together")
    try:
        if args.candidate:
            result = verified_candidate(args.work_root, args.edam, args.core_root,
                                        args.compilation_unit, args.scratch_root)
        else:
            result = import_edam(args.work_root, args.edam, args.core_root,
                                 args.compilation_unit)
    except (EdamImportError, probe.ProbeError, OSError, ValueError) as exc:
        print(json.dumps({"schema": CANDIDATE_SCHEMA if args.candidate else SCHEMA,
                          "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())

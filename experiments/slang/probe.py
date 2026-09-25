"""Pinned Slang experiment for local, trusted SystemVerilog sources.

This is an evaluation harness, not an import service or security sandbox.
Run it with this directory's uv.lock and a finite process timeout.
"""

import argparse
import hashlib
import importlib.metadata
import json
import pathlib
import re
import sys


SCHEMA = "unifpga.slang-probe/v1"
ELABORATION_SCHEMA = "unifpga.slang-elaboration/v1"
SLANG_VERSION = "11.0.0"
MAX_FILES = 256
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_GRAPH_SYMBOLS = 100000
MAX_GRAPH_INSTANCES = 4096
MAX_INSTANCE_ITEMS = 1024
MAX_GRAPH_ITEMS = 8192
MAX_GRAPH_BYTES = 8 * 1024 * 1024
MAX_FACT_TEXT_BYTES = 4096
_DEFINE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*(?:=[^\r\n]*)?$")


class ProbeError(ValueError):
    pass


def _path(root, value, kind):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value or \
            any(ord(ch) < 32 for ch in value):
        raise ProbeError("invalid {} path {!r}".format(kind, value))
    parts = value.split("/")
    if value != "." and any(part in ("", ".", "..") for part in parts):
        raise ProbeError("invalid {} path {!r}".format(kind, value))
    if pathlib.PurePosixPath(value).is_absolute():
        raise ProbeError("absolute {} path is forbidden".format(kind))
    path = root / value
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ProbeError("symlink in {} path {!r}".format(kind, value))
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ProbeError("{} path escapes root: {!r}".format(kind, value))
    if kind == "source" and not resolved.is_file():
        raise ProbeError("source is not a file: {!r}".format(value))
    if kind == "include directory" and not resolved.is_dir():
        raise ProbeError("include directory is not a directory: {!r}".format(value))
    return resolved


def load_request(root, request_path):
    root = root.resolve()
    with request_path.open("rb") as stream:
        raw = stream.read(64 * 1024 + 1)
    if len(raw) > 64 * 1024:
        raise ProbeError("request exceeds 64 KiB")
    try:
        def unique_fields(pairs):
            obj = {}
            for key, value in pairs:
                if key in obj:
                    raise ProbeError("duplicate request field {!r}".format(key))
                obj[key] = value
            return obj
        request = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_fields)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ProbeError("invalid UTF-8 JSON request: {}".format(exc)) from exc
    if not isinstance(request, dict) or set(request) != {
            "schema", "sources", "include_dirs", "defines", "top", "compilation_unit"}:
        raise ProbeError("request fields do not match {}".format(SCHEMA))
    if request["schema"] != SCHEMA or request["compilation_unit"] not in ("separate", "single"):
        raise ProbeError("unsupported schema or compilation-unit policy")
    sources = request["sources"]
    includes = request["include_dirs"]
    defines = request["defines"]
    top = request["top"]
    if not isinstance(sources, list) or not 0 < len(sources) <= MAX_FILES or \
            not isinstance(includes, list) or len(includes) > MAX_FILES or \
            not isinstance(defines, list) or len(defines) > MAX_FILES or \
            not isinstance(top, str) or not top.strip() or len(top) > 256:
        raise ProbeError("invalid source, include, define, or top inventory")
    source_paths = [_path(root, item, "source") for item in sources]
    include_paths = [_path(root, item, "include directory") for item in includes]
    if len(set(source_paths)) != len(source_paths) or len(set(include_paths)) != len(include_paths):
        raise ProbeError("duplicate source or include directory")
    if any(not isinstance(item, str) or len(item) > 1024 or not _DEFINE.fullmatch(item)
           for item in defines) or len(set(item.split("=", 1)[0] for item in defines)) != len(defines):
        raise ProbeError("invalid or duplicate macro definition")
    return request, source_paths, include_paths, hashlib.sha256(raw).hexdigest()


def _digest(path):
    size = 0
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                raise ProbeError("file exceeds 4 MiB: {}".format(path.name))
            digest.update(chunk)
    return {"bytes": size, "sha256": digest.hexdigest()}


def _inventory(root, paths):
    if len(paths) > MAX_FILES:
        raise ProbeError("more than 256 source/include files were read")
    inventory = []
    total = 0
    for path in paths:
        if path.is_symlink() or not path.is_relative_to(root) or not path.is_file():
            raise ProbeError("frontend read an unsafe file: {!r}".format(path.name))
        info = _digest(path)
        total += info["bytes"]
        if total > MAX_TOTAL_BYTES:
            raise ProbeError("source/include bytes exceed 32 MiB")
        inventory.append({"path": path.relative_to(root).as_posix(), **info})
    return inventory


def _diagnostics(root, manager, diagnostics):
    return [{"code": str(diag.code), "severity": "error" if diag.isError() else "warning",
             "location": _location(root, manager, diag.location)} for diag in diagnostics]


def _location(root, manager, location):
    """A source coordinate is useful only when it names an admitted file."""
    try:
        path = pathlib.Path(manager.getFullPath(location.buffer)).resolve()
        if path.is_file() and path.is_relative_to(root):
            return {"path": path.relative_to(root).as_posix(),
                    "line": manager.getLineNumber(location),
                    "column": manager.getColumnNumber(location)}
    except (OSError, RuntimeError, ValueError):
        pass
    return None


def _range(root, manager, source_range):
    if source_range is None:
        return None
    start = _location(root, manager, source_range.start)
    end = _location(root, manager, source_range.end)
    if start is None or end is None or start["path"] != end["path"]:
        return None
    return {"path": start["path"],
            "start": {"line": start["line"], "column": start["column"]},
            "end": {"line": end["line"], "column": end["column"]}}


def _elaboration(root, manager, top, ast):
    """Project bounded elaborated facts, without serializing Slang's AST.

    Widths and values are facts for this exact macro/parameter selection. A
    connection expression is only a possible direct net edge when Slang says
    it is a named value or assignment; other expressions remain unresolved.
    """
    instances = []
    findings = []
    visited_symbols = 0
    projected_items = 0

    def fact_text(value):
        result = str(value)
        if len(result.encode("utf-8")) > MAX_FACT_TEXT_BYTES:
            raise ProbeError("elaborated fact exceeds text limit")
        return result

    def port_fact(port):
        row = {"name": fact_text(port.name), "symbol_kind": fact_text(port.kind).split(".")[-1],
               "location": _location(root, manager, port.location),
               "direction": None, "type": None, "evaluated_bit_width": None,
               "signed": None, "four_state": None,
               "interface_definition": None, "modport": None}
        if isinstance(port, ast.PortSymbol):
            row["direction"] = {"In": "input", "Out": "output", "InOut": "inout",
                                "Ref": "ref"}.get(port.direction.name, port.direction.name.lower())
            dtype = port.type
            row["type"] = fact_text(dtype)
            row["evaluated_bit_width"] = int(dtype.bitWidth) if dtype.isFixedSize else None
            row["signed"] = bool(dtype.isSigned)
            row["four_state"] = bool(dtype.isFourState)
        elif isinstance(port, ast.InterfacePortSymbol):
            row["interface_definition"] = fact_text(port.interfaceDef.name) if port.interfaceDef else None
            row["modport"] = fact_text(port.modport) if port.modport else None
        else:
            findings.append({"code": "unprojected_port_kind", "symbol_kind": row["symbol_kind"],
                             "location": row["location"]})
        return row

    def parameter_fact(parameter):
        is_type = isinstance(parameter, ast.TypeParameterSymbol)
        value = None if is_type else parameter.value
        return {"name": fact_text(parameter.name), "kind": "type" if is_type else "value",
                "type": fact_text(parameter.targetType.type if is_type else parameter.type),
                "evaluated_value": None if is_type else fact_text(value),
                "has_unknown_bits": None if is_type else bool(value.hasUnknown()),
                "is_local": bool(parameter.isLocalParam),
                "is_overridden": bool(parameter.isOverridden),
                "location": _location(root, manager, parameter.location)}

    def connection_fact(connection):
        expression = connection.expression
        kind = fact_text(expression.kind).split(".")[-1] if expression else None
        reference = None
        if expression and kind in ("NamedValue", "Assignment"):
            symbol = expression.getSymbolReference()
            if symbol is not None:
                reference = fact_text(symbol.hierarchicalPath)
        interface, modport = connection.ifaceConn
        return {"port": fact_text(connection.port.name), "expression_kind": kind,
                "source_range": _range(root, manager, expression.sourceRange) if expression else None,
                "direct_symbol_reference": reference,
                "interface_instance": fact_text(interface.hierarchicalPath) if interface else None,
                "modport": fact_text(modport.name) if modport else None}

    def visit(symbol, parent_path, depth):
        nonlocal visited_symbols, projected_items
        visited_symbols += 1
        if visited_symbols > MAX_GRAPH_SYMBOLS or depth > 64:
            raise ProbeError("elaborated graph exceeds symbol or depth limit")
        if isinstance(symbol, ast.InstanceSymbol):
            if len(instances) >= MAX_GRAPH_INSTANCES:
                raise ProbeError("elaborated graph exceeds instance limit")
            body = symbol.body
            ports, parameters, connections = body.portList, body.parameters, symbol.portConnections
            if max(len(ports), len(parameters), len(connections)) > MAX_INSTANCE_ITEMS:
                raise ProbeError("elaborated instance exceeds port or parameter limit")
            projected_items += len(ports) + len(parameters) + len(connections)
            if projected_items > MAX_GRAPH_ITEMS:
                raise ProbeError("elaborated graph exceeds projected item limit")
            path = fact_text(symbol.hierarchicalPath)
            instances.append({"path": path, "parent_instance": parent_path,
                              "name": fact_text(symbol.name), "definition": fact_text(body.definition.name),
                              "kind": "interface" if symbol.isInterface else
                                      "module" if symbol.isModule else "other",
                              "location": _location(root, manager, symbol.location),
                              "ports": [port_fact(port) for port in ports],
                              "parameters": [parameter_fact(p) for p in parameters],
                              "connections": [connection_fact(c) for c in connections]})
            for child in body:
                visit(child, path, depth + 1)
        elif isinstance(symbol, (ast.GenerateBlockSymbol, ast.GenerateBlockArraySymbol)):
            for child in symbol:
                visit(child, parent_path, depth + 1)

    visit(top, None, 0)
    result = {"schema": ELABORATION_SCHEMA,
            "scope": "elaborated-interface-and-instance-facts",
            "projection_status": "partial" if findings else "complete",
            "semantic_completeness": "unproven",
            "instances": instances, "findings": findings}
    if len(json.dumps(result, separators=(",", ":")).encode("utf-8")) > MAX_GRAPH_BYTES:
        raise ProbeError("elaborated graph exceeds output byte limit")
    return result


def run(root, request_path):
    root = root.resolve()
    request, sources, includes, request_digest = load_request(root, request_path)
    source_before = _inventory(root, sources)

    try:
        version = importlib.metadata.version("pyslang")
    except importlib.metadata.PackageNotFoundError as exc:
        raise ProbeError("pyslang {} is not installed".format(SLANG_VERSION)) from exc
    if version != SLANG_VERSION:
        raise ProbeError("expected pyslang {}, found {}".format(SLANG_VERSION, version))
    try:
        import pyslang
        from pyslang import ast, parsing, syntax
    except ImportError as exc:
        raise ProbeError("pyslang {} cannot be loaded: {}".format(SLANG_VERSION, exc)) from exc

    manager = pyslang.SourceManager()
    for path in includes:
        manager.addUserDirectories(str(path))
    preprocess = parsing.PreprocessorOptions()
    preprocess.predefines = request["defines"]
    preprocess.maxIncludeDepth = 64
    options = pyslang.Bag([preprocess])
    if request["compilation_unit"] == "single":
        trees = [syntax.SyntaxTree.fromFiles([str(path) for path in sources], manager, options)]
    else:
        trees = [syntax.SyntaxTree.fromFile(str(path), manager, options) for path in sources]
    compilation_options = ast.CompilationOptions()
    compilation_options.topModules = {request["top"]}
    compilation_options.maxInstanceDepth = 64
    compilation_options.maxGenerateSteps = 100000
    compilation_options.maxConstexprSteps = 100000
    compilation = ast.Compilation(pyslang.Bag([compilation_options]))
    for tree in trees:
        compilation.addSyntaxTree(tree)
    root_symbol = compilation.getRoot()
    parse_diagnostics = _diagnostics(root, manager, compilation.getParseDiagnostics())
    semantic_diagnostics = _diagnostics(root, manager, compilation.getSemanticDiagnostics())

    observed = []
    for buffer in manager.getAllBuffers():
        path = pathlib.Path(manager.getFullPath(buffer))
        if path == pathlib.Path(".") or (
                not path.is_absolute() and re.fullmatch(r"<unnamed_buffer[0-9]+>", path.name)):
            continue  # Slang's synthetic compilation buffers
        observed.append(path.resolve())
    input_files = sorted(set(observed), key=lambda path: path.as_posix())
    files = _inventory(root, input_files)
    if _inventory(root, sources) != source_before:
        raise ProbeError("source changed during frontend run")
    tops = sorted(str(instance.name) for instance in root_symbol.topInstances)
    diagnostics = parse_diagnostics + semantic_diagnostics
    accepted = request["top"] in tops and not any(row["severity"] == "error" for row in diagnostics)
    elaboration = None
    if accepted:
        top_symbol = next(instance for instance in root_symbol.topInstances
                          if str(instance.name) == request["top"])
        elaboration = _elaboration(root, manager, top_symbol, ast)
    return {"schema": SCHEMA, "pyslang_version": version,
            "request_sha256": request_digest, "compilation_unit": request["compilation_unit"],
            "top": request["top"], "elaborated_tops": tops,
            "sources": source_before, "read_files": files,
            "parse_diagnostics": parse_diagnostics, "semantic_diagnostics": semantic_diagnostics,
            "accepted": accepted, "elaboration": elaboration}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=pathlib.Path)
    parser.add_argument("--request", required=True, type=pathlib.Path)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if not root.is_dir():
        parser.error("root must be a directory")
    try:
        result = run(root, args.request)
    except (OSError, ProbeError) as exc:
        print(json.dumps({"schema": SCHEMA, "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["accepted"] else 3


if __name__ == "__main__":
    sys.exit(main())

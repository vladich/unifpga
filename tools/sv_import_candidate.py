"""Produce a reviewable import candidate from a trusted local SV source set.

The pinned Slang frontend owns parsing and elaboration. This module projects
its result into stable catalog-facing facts; it neither publishes a component
nor claims that RTL ports have virtual-device semantics.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys

from experiments.slang import probe


SCHEMA = "unifpga.sv-import-candidate/v1"
MAX_REPORT_BYTES = 2 * 1024 * 1024
REVIEW_TOPICS = (
    "frontend_diagnostics",
    "clock_reset_and_domains",
    "protocol_and_handshake",
    "latency_and_timing",
    "virtual_device_binding",
    "electrical_and_board_constraints",
    "vendor_ip_and_external_dependencies",
    "runtime_assets_and_memories",
)


class CandidateError(ValueError):
    pass


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def candidate_from_probe(result):
    """Project one accepted elaboration without inventing semantic roles.

    This boundary consumes the exact result of probe.run, rather than a loose
    source-text scan. A partial Slang projection remains visible and blocks
    structural readiness; all candidates still require semantic review.
    """
    if not result["accepted"] or result["elaboration"] is None:
        raise CandidateError("SystemVerilog frontend did not accept the selected top")
    graph = result["elaboration"]
    if graph["schema"] != probe.ELABORATION_SCHEMA:
        raise CandidateError("unsupported elaboration facts schema")
    top_nodes = [item for item in graph["instances"]
                 if item["parent_instance"] is None]
    if len(top_nodes) != 1 or top_nodes[0]["path"] != result["top"] or \
            top_nodes[0]["kind"] != "module":
        raise CandidateError("selected top is not one elaborated module")
    top = top_nodes[0]
    if not {item["path"] for item in result["sources"]} <= \
            {item["path"] for item in result["read_files"]}:
        raise CandidateError("frontend did not account for every source file")

    ports = []
    blockers = []
    for port in top["ports"]:
        kind = port["symbol_kind"]
        width = port["evaluated_bit_width"]
        direction = port["direction"]
        if kind == "Port" and direction in ("input", "output") and \
                isinstance(width, int) and width > 0:
            shape = "fixed_width"
        elif kind == "InterfacePort":
            shape = "interface"
        elif kind == "Port" and direction in ("inout", "ref"):
            shape = "bidirectional_or_ref"
        else:
            shape = "unresolved"
        if shape != "fixed_width":
            blockers.append({"code": "top_port_requires_adapter_review",
                             "port": port["name"], "shape": shape})
        ports.append({"name": port["name"], "direction": direction,
                      "shape": shape, "evaluated_bit_width": width,
                      "type": port["type"], "signed": port["signed"],
                      "four_state": port["four_state"],
                      "interface_definition": port["interface_definition"],
                      "modport": port["modport"], "location": port["location"],
                      "semantic_role": None})

    if graph["projection_status"] != "complete":
        blockers.append({"code": "partial_elaboration_projection",
                         "findings": graph["findings"]})
    facts = {
        "frontend": {"name": "slang", "version": result["pyslang_version"],
                     "request_schema": result["schema"],
                     "request_sha256": result["request_sha256"],
                     "compilation_unit": result["compilation_unit"]},
        "source_files": result["read_files"],
        "top": {"name": result["top"], "definition": top["definition"],
                "location": top["location"], "ports": ports,
                "parameters": top["parameters"]},
        "hierarchy": {"instance_count": len(graph["instances"]),
                      "definitions": sorted({item["definition"] for item in graph["instances"]}),
                      "projection_status": graph["projection_status"]},
    }
    report = {"schema": SCHEMA,
              "state": "structurally_blocked" if blockers else "needs_semantic_review",
              "source_scope": "frontend_read_files_only",
              **facts,
              "parse_warnings": result["parse_diagnostics"],
              "semantic_warnings": result["semantic_diagnostics"],
              "blockers": blockers,
              "review_topics": list(REVIEW_TOPICS)}
    # The digest covers every report field except the digest itself.
    report["candidate_sha256"] = hashlib.sha256(_canonical(report)).hexdigest()
    if len(_canonical(report)) > MAX_REPORT_BYTES:
        raise CandidateError("import candidate exceeds 2 MiB")
    return report


def run(root, request_path):
    return candidate_from_probe(probe.run(Path(root), Path(request_path)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path,
                        help="trusted local source root")
    parser.add_argument("--request", required=True, type=Path,
                        help="pinned Slang frontend request JSON")
    args = parser.parse_args(argv)
    if not args.root.is_dir():
        parser.error("root must be a directory")
    try:
        report = run(args.root, args.request)
    except (CandidateError, probe.ProbeError, OSError) as exc:
        print(json.dumps({"schema": SCHEMA, "error": str(exc)}, sort_keys=True))
        return 2
    sys.stdout.buffer.write(_canonical(report) + b"\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

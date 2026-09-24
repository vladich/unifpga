"""Review source-level catalog changes between two exact Git revisions.

This is a staging report for the planned importer. It does not validate domain
references, create database revisions, or authorize catalog publication.
"""

import argparse
import json
import sys

from tools.catalog_snapshot import CatalogSnapshotError, capture_catalog_revision


CANDIDATE_SCHEMA = "unifpga.catalog-source-candidate/v1"


def _source(snapshot):
    return {"commit": snapshot["git"]["commit"],
            "tree": snapshot["git"]["tree"],
            "source_sha256": snapshot["source_sha256"],
            "semantic_sha256": snapshot["semantic_sha256"],
            "file_count": snapshot["file_count"]}


def _digests(item):
    if item is None:
        return None
    return {"size": item["size"], "raw_sha256": item["raw_sha256"],
            "semantic_sha256": item["semantic_sha256"]}


def compare_catalog_revisions(repo, base_commit, candidate_commit, source_root="config"):
    """Return a deterministic, nonpublishing source-diff candidate report.

    Removed source files are review findings, never inferred withdrawals.
    Renames remain an add plus a removal until a reviewer supplies identity
    evidence. The report never marks the candidate as accepted.
    """
    base = capture_catalog_revision(repo, base_commit, source_root)
    report = {"schema": CANDIDATE_SCHEMA, "source_root": source_root,
              "base": _source(base), "candidate_commit": candidate_commit,
              "state": "candidate", "validation": {"source": "passed",
                                                   "domain": "not_run"},
              "candidate": None, "summary": None, "changes": [], "findings": []}
    try:
        candidate = capture_catalog_revision(repo, candidate_commit, source_root)
    except CatalogSnapshotError as exc:
        report["state"] = "invalid_source"
        report["validation"]["source"] = "failed"
        report["findings"].append({"code": "invalid_source", "severity": "error",
                                   "detail": str(exc)})
        return report

    report["candidate"] = _source(candidate)
    before = {item["path"]: item for item in base["files"]}
    after = {item["path"]: item for item in candidate["files"]}
    counts = {"added": 0, "removed": 0, "semantic_changed": 0,
              "formatting_only": 0, "unchanged": 0}
    for path in sorted(before.keys() | after.keys()):
        old = before.get(path)
        new = after.get(path)
        if old is None:
            kind = "added"
        elif new is None:
            kind = "removed"
        elif old["raw_sha256"] == new["raw_sha256"]:
            kind = "unchanged"
        elif old["semantic_sha256"] == new["semantic_sha256"]:
            kind = "formatting_only"
        else:
            kind = "semantic_changed"
        counts[kind] += 1
        if kind == "unchanged":
            continue
        report["changes"].append({"path": path, "kind": kind,
                                  "before": _digests(old), "after": _digests(new)})
        if kind == "removed":
            report["findings"].append({"code": "source_removed",
                                       "severity": "review", "path": path,
                                       "detail": "Removal is not an accepted withdrawal or rename"})
    report["summary"] = counts
    report["findings"].append({"code": "domain_validation_pending",
                               "severity": "review",
                               "detail": "Source comparison does not validate catalog references or publish revisions"})
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default="config",
                        help="repository-relative catalog root (default: config)")
    parser.add_argument("--repo", default=".", help="Git checkout (default: current directory)")
    parser.add_argument("--base", required=True, help="exact base commit ID")
    parser.add_argument("--candidate", required=True, help="exact candidate commit ID")
    args = parser.parse_args(argv)
    try:
        report = compare_catalog_revisions(args.repo, args.base, args.candidate, args.root)
    except CatalogSnapshotError as exc:
        parser.error(str(exc))
    json.dump(report, sys.stdout, sort_keys=True, separators=(",", ":"))
    sys.stdout.write("\n")
    return 2 if report["state"] == "invalid_source" else 0


if __name__ == "__main__":
    sys.exit(main())

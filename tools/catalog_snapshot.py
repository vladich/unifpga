"""Capture exact YAML sources and semantic content for catalog import staging.

This is a read-only source inventory, not a domain validator or an accepted
catalog publication. The current YAML readers remain authoritative. Version 1
uses typed canonical values because existing YAML contains integer mapping keys;
ordinary JSON object serialization would conflate those with string keys.
"""

import argparse
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys

import yaml

from config.init import _UniqueKeyLoader


SNAPSHOT_SCHEMA = "unifpga.catalog-source-snapshot/v1"
MAX_FILES = 100000
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_SEMANTIC_NODES = 500000
MAX_TOTAL_SEMANTIC_NODES = 10000000
MAX_TREE_LISTING_BYTES = 64 * 1024 * 1024
MAX_PATH_BYTES = 1024


class CatalogSnapshotError(ValueError):
    """The source tree cannot be captured safely and unambiguously."""


def _encoded(value, active, budget=None):
    """Return a typed, order-independent representation of one YAML value."""
    if budget is None:
        budget = [0]
    budget[0] += 1
    if budget[0] > MAX_SEMANTIC_NODES:
        raise CatalogSnapshotError("YAML alias expansion exceeds semantic node limit")
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["int", str(value)]
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CatalogSnapshotError("non-finite YAML number")
        return ["float", value.hex()]
    if isinstance(value, str):
        return ["str", value]
    if not isinstance(value, (dict, list)):
        raise CatalogSnapshotError("unsupported YAML value type {}".format(type(value).__name__))
    identity = id(value)
    if identity in active:
        raise CatalogSnapshotError("cyclic YAML alias")
    active.add(identity)
    try:
        if isinstance(value, list):
            return ["list", [_encoded(item, active, budget) for item in value]]
        entries = []
        for key, item in value.items():
            encoded_key = _encoded(key, active, budget)
            if encoded_key[0] not in ("null", "bool", "int", "float", "str"):
                raise CatalogSnapshotError("non-scalar YAML mapping key")
            entries.append((encoded_key, _encoded(item, active, budget)))
        entries.sort(key=lambda entry: _canonical_bytes(entry[0]))
        return ["map", entries]
    finally:
        active.remove(identity)


def _canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _safe_path(path):
    if (not path or path.startswith("/") or "\\" in path or ":" in path or
            any(part in ("", ".", "..") for part in path.split("/")) or
            any(ord(character) < 32 or ord(character) == 127 for character in path) or
            len(path.encode("utf-8")) > MAX_PATH_BYTES):
        raise CatalogSnapshotError("unsafe catalog source path: {!r}".format(path))
    return path


def _paths(root):
    if os.path.islink(root) or not os.path.isdir(root):
        raise CatalogSnapshotError("catalog root must be a non-symlink directory: {}".format(root))
    def failed(exc):
        raise CatalogSnapshotError("cannot traverse catalog source: {}".format(exc)) from exc

    for directory, dirs, files in os.walk(root, followlinks=False, onerror=failed):
        dirs.sort()
        files.sort()
        for name in dirs + files:
            path = os.path.join(directory, name)
            if os.path.islink(path):
                raise CatalogSnapshotError("catalog source contains symlink: {}".format(path))
        for name in files:
            if name.endswith(".yml"):
                yield os.path.join(directory, name)


def _read_file(path):
    # Nonblocking open lets fstat reject a named pipe without waiting for a
    # writer; it has no effect on regular files.
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise CatalogSnapshotError("catalog source is not a regular file: {}".format(path))
            if before.st_size > MAX_FILE_BYTES:
                raise CatalogSnapshotError("catalog source exceeds file limit: {}".format(path))
            raw = stream.read(MAX_FILE_BYTES + 1)
            after = os.fstat(stream.fileno())
            if len(raw) > MAX_FILE_BYTES or before.st_size != len(raw):
                raise CatalogSnapshotError("catalog source size changed or exceeds limit: {}".format(path))
            if (before.st_mtime_ns, before.st_ctime_ns, before.st_ino) != (
                    after.st_mtime_ns, after.st_ctime_ns, after.st_ino):
                raise CatalogSnapshotError("catalog source changed while reading: {}".format(path))
    except OSError as exc:
        raise CatalogSnapshotError("cannot read catalog source {}: {}".format(path, exc)) from exc
    return raw


def _manifest(records, documents=None):
    """Build one source manifest from (root-relative path, exact bytes) pairs."""
    files = []
    total_bytes = 0
    total_nodes = 0
    for rel, raw in records:
        _safe_path(rel)
        if len(files) >= MAX_FILES:
            raise CatalogSnapshotError("catalog exceeds file count limit")
        if len(raw) > MAX_FILE_BYTES:
            raise CatalogSnapshotError("catalog source exceeds file limit: {}".format(rel))
        total_bytes += len(raw)
        if total_bytes > MAX_TOTAL_BYTES:
            raise CatalogSnapshotError("catalog exceeds total byte limit")
        try:
            document = yaml.load(raw, Loader=_UniqueKeyLoader)
            nodes = [0]
            encoded = _encoded(document, set(), nodes)
            total_nodes += nodes[0]
            if total_nodes > MAX_TOTAL_SEMANTIC_NODES:
                raise CatalogSnapshotError("catalog exceeds semantic node limit")
            canonical = _canonical_bytes(encoded)
            if len(canonical) > MAX_FILE_BYTES:
                raise CatalogSnapshotError("expanded YAML exceeds semantic byte limit")
            semantic_digest = hashlib.sha256(canonical).hexdigest()
        except (yaml.YAMLError, CatalogSnapshotError, UnicodeError, RecursionError) as exc:
            raise CatalogSnapshotError("{}: {}".format(rel, exc)) from exc
        if documents is not None:
            documents[rel] = document
        files.append({"path": rel, "size": len(raw),
                      "raw_sha256": hashlib.sha256(raw).hexdigest(),
                      "semantic_sha256": semantic_digest})
    files.sort(key=lambda item: item["path"])
    return {"schema": SNAPSHOT_SCHEMA,
            "parser": {"name": "PyYAML", "version": yaml.__version__},
            "file_count": len(files),
            "total_bytes": total_bytes,
            "source_sha256": _digest([[item["path"], item["raw_sha256"]] for item in files]),
            "semantic_sha256": _digest([[item["path"], item["semantic_sha256"]] for item in files]),
            "files": files}


def capture_catalog(root):
    """Capture working-tree YAML; this result has no exact revision binding."""
    root = os.path.abspath(root)
    return _manifest((os.path.relpath(path, root).replace(os.sep, "/"),
                      _read_file(path)) for path in _paths(root))


def _git(repo, *args, max_output_bytes=4096):
    try:
        proc = subprocess.Popen(["git", "--no-replace-objects", "-C", repo] + list(args),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except OSError as exc:
        raise CatalogSnapshotError("cannot run Git: {}".format(exc)) from exc
    try:
        output = proc.stdout.read(max_output_bytes + 1)
    finally:
        proc.stdout.close()
    status = proc.wait(timeout=10)
    if len(output) > max_output_bytes:
        raise CatalogSnapshotError("Git {} output exceeds limit".format(args[0]))
    if status:
        raise CatalogSnapshotError("Git {} failed: {}".format(
            args[0], output.decode("utf-8", "replace").strip()[:500]))
    return output


def _git_entries(repo, tree):
    """Read typed tree entries with NUL-delimited names; reject non-files."""
    output = _git(repo, "ls-tree", "-r", "-z", "--long", tree,
                  max_output_bytes=MAX_TREE_LISTING_BYTES)
    entries = []
    total_bytes = 0
    for record in output.split(b"\0"):
        if not record:
            continue
        try:
            header, raw_path = record.split(b"\t", 1)
            mode, kind, oid, size = header.split()
            path = raw_path.decode("utf-8")
            size = int(size)
        except (ValueError, UnicodeError) as exc:
            raise CatalogSnapshotError("malformed Git tree entry") from exc
        _safe_path(path)
        if mode == b"120000" or kind != b"blob":
            raise CatalogSnapshotError("catalog tree contains symlink or non-file: {}".format(path))
        if not path.endswith(".yml"):
            continue
        if len(entries) >= MAX_FILES or size > MAX_FILE_BYTES:
            raise CatalogSnapshotError("catalog Git tree exceeds file limit: {}".format(path))
        total_bytes += size
        if total_bytes > MAX_TOTAL_BYTES:
            raise CatalogSnapshotError("catalog Git tree exceeds total byte limit")
        entries.append((path, oid.decode("ascii"), size))
    return entries


def _git_blobs(repo, entries):
    """Stream bounded blobs through one Git process, with no worktree reads."""
    if not entries:
        return
    try:
        proc = subprocess.Popen(["git", "--no-replace-objects", "-C", repo,
                                 "cat-file", "--batch"],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise CatalogSnapshotError("cannot run Git cat-file: {}".format(exc)) from exc
    complete = False
    try:
        for path, oid, expected_size in entries:
            proc.stdin.write(oid.encode("ascii") + b"\n")
            proc.stdin.flush()
            header = proc.stdout.readline().strip().split()
            if len(header) != 3 or header[0] != oid.encode("ascii") or header[1] != b"blob":
                raise CatalogSnapshotError("Git blob header mismatch: {}".format(path))
            try:
                size = int(header[2])
            except ValueError as exc:
                raise CatalogSnapshotError("Git blob size is malformed: {}".format(path)) from exc
            if size != expected_size or size > MAX_FILE_BYTES:
                raise CatalogSnapshotError("Git blob size mismatch: {}".format(path))
            raw = proc.stdout.read(size)
            if len(raw) != size or proc.stdout.read(1) != b"\n":
                raise CatalogSnapshotError("Git blob truncated: {}".format(path))
            yield path, raw
        complete = True
    finally:
        proc.stdin.close()
        proc.stdout.close()
        if proc.wait(timeout=10) and complete:
            raise CatalogSnapshotError("Git cat-file failed")


def capture_catalog_revision(repo, commit, source_root="config", *, with_documents=False):
    """Capture YAML from one exact Git commit, independent of worktree edits."""
    if not isinstance(commit, str) or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit):
        raise CatalogSnapshotError("revision must be an exact Git commit ID")
    if (not isinstance(source_root, str) or not source_root or
            any(part in ("", ".", "..") for part in source_root.split("/")) or
            not re.fullmatch(r"[A-Za-z0-9_./-]+", source_root)):
        raise CatalogSnapshotError("source root must be a safe repository-relative directory")
    repo = os.path.abspath(repo)
    verified = _git(repo, "rev-parse", "--verify", commit + "^{commit}").strip().decode("ascii")
    if verified != commit:
        raise CatalogSnapshotError("revision does not resolve to the exact commit")
    tree = _git(repo, "rev-parse", "--verify", commit + ":" + source_root).strip().decode("ascii")
    if _git(repo, "cat-file", "-t", tree).strip() != b"tree":
        raise CatalogSnapshotError("source root is not a Git tree")
    entries = _git_entries(repo, tree)
    documents = {} if with_documents else None
    manifest = _manifest(_git_blobs(repo, entries), documents)
    manifest["git"] = {"commit": commit, "source_root": source_root, "tree": tree}
    return (manifest, documents) if with_documents else manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", help="live directory, or repository-relative directory with --revision")
    parser.add_argument("--revision", help="exact Git commit ID to capture")
    parser.add_argument("--repo", default=".", help="Git checkout for --revision (default: current directory)")
    args = parser.parse_args(argv)
    try:
        manifest = (capture_catalog_revision(args.repo, args.revision, args.root)
                    if args.revision else capture_catalog(args.root))
    except CatalogSnapshotError as exc:
        parser.error(str(exc))
    json.dump(manifest, sys.stdout, sort_keys=True, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

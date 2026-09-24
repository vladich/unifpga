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
import stat
import sys

import yaml

from config.init import _UniqueKeyLoader


SNAPSHOT_SCHEMA = "unifpga.catalog-source-snapshot/v1"
MAX_FILES = 100000
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024


class CatalogSnapshotError(ValueError):
    """The source tree cannot be captured safely and unambiguously."""


def _encoded(value, active):
    """Return a typed, order-independent representation of one YAML value."""
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
            return ["list", [_encoded(item, active) for item in value]]
        entries = []
        for key, item in value.items():
            encoded_key = _encoded(key, active)
            if encoded_key[0] not in ("null", "bool", "int", "float", "str"):
                raise CatalogSnapshotError("non-scalar YAML mapping key")
            entries.append((encoded_key, _encoded(item, active)))
        entries.sort(key=lambda entry: _canonical_bytes(entry[0]))
        return ["map", entries]
    finally:
        active.remove(identity)


def _canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


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


def capture_catalog(root):
    """Return a deterministic versioned manifest for all ``*.yml`` under root.

    A source digest includes path and raw bytes; a semantic digest includes path
    and typed parsed values. Neither digest incorporates host paths or timestamps.
    The caller must bind the result to an exact Git commit before publication.
    """
    root = os.path.abspath(root)
    files = []
    total_bytes = 0
    for path in _paths(root):
        if len(files) >= MAX_FILES:
            raise CatalogSnapshotError("catalog exceeds file count limit")
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        raw = _read_file(path)
        total_bytes += len(raw)
        if total_bytes > MAX_TOTAL_BYTES:
            raise CatalogSnapshotError("catalog exceeds total byte limit")
        try:
            document = yaml.load(raw, Loader=_UniqueKeyLoader)
            semantic_digest = _digest(_encoded(document, set()))
        except (yaml.YAMLError, CatalogSnapshotError, UnicodeError, RecursionError) as exc:
            raise CatalogSnapshotError("{}: {}".format(rel, exc)) from exc
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", help="catalog source directory, normally config/")
    args = parser.parse_args(argv)
    try:
        manifest = capture_catalog(args.root)
    except CatalogSnapshotError as exc:
        parser.error(str(exc))
    json.dump(manifest, sys.stdout, sort_keys=True, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

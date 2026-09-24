"""The source snapshot records exact bytes without confusing YAML semantics."""

import os

import pytest

from tools.catalog_snapshot import CatalogSnapshotError, capture_catalog


def _write(root, name, contents):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    return path


def test_snapshot_is_order_independent_but_preserves_raw_sources(tmp_path):
    path = _write(tmp_path, "nested/item.yml", "value: {b: 2, a: 1}\n")
    first = capture_catalog(tmp_path)
    path.write_text("# comment\nvalue: {a: 1, b: 2}\n", encoding="utf-8")
    second = capture_catalog(tmp_path)
    assert first["schema"] == "unifpga.catalog-source-snapshot/v1"
    assert first["file_count"] == 1
    assert first["files"][0]["path"] == "nested/item.yml"
    assert first["source_sha256"] != second["source_sha256"]
    assert first["semantic_sha256"] == second["semantic_sha256"]
    assert second == capture_catalog(tmp_path)


def test_snapshot_preserves_list_order_and_yaml_key_types(tmp_path):
    path = _write(tmp_path, "item.yml", "ports: [a, b]\n")
    original = capture_catalog(tmp_path)["semantic_sha256"]
    path.write_text("ports: [b, a]\n", encoding="utf-8")
    assert capture_catalog(tmp_path)["semantic_sha256"] != original
    path.write_text("values: {5: pin}\n", encoding="utf-8")
    numeric_key = capture_catalog(tmp_path)["semantic_sha256"]
    path.write_text("values: {'5': pin}\n", encoding="utf-8")
    assert capture_catalog(tmp_path)["semantic_sha256"] != numeric_key


def test_snapshot_identity_includes_path_and_all_files(tmp_path):
    _write(tmp_path, "b.yml", "item: 1\n")
    _write(tmp_path, "a.yml", "item: 1\n")
    first = capture_catalog(tmp_path)
    assert [entry["path"] for entry in first["files"]] == ["a.yml", "b.yml"]
    (tmp_path / "b.yml").rename(tmp_path / "c.yml")
    second = capture_catalog(tmp_path)
    assert first["source_sha256"] != second["source_sha256"]
    assert first["semantic_sha256"] != second["semantic_sha256"]


@pytest.mark.parametrize("content,diagnostic", [
    ("item: 1\nitem: 2\n", "duplicate key"),
    ("item: .nan\n", "non-finite"),
    ("item: &self [*self]\n", "cyclic YAML alias"),
    ("item: 2026-09-24\n", "unsupported YAML value type"),
])
def test_snapshot_rejects_ambiguous_or_noncanonical_yaml(tmp_path, content, diagnostic):
    _write(tmp_path, "bad.yml", content)
    with pytest.raises(CatalogSnapshotError, match=diagnostic):
        capture_catalog(tmp_path)


def test_snapshot_rejects_symlinked_files_and_directories(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-outside.yml")
    outside.write_text("item: 1\n", encoding="utf-8")
    try:
        os.symlink(outside, tmp_path / "linked.yml")
        with pytest.raises(CatalogSnapshotError, match="symlink"):
            capture_catalog(tmp_path)
        (tmp_path / "linked.yml").unlink()
        directory = tmp_path / "real"
        directory.mkdir()
        os.symlink(directory, tmp_path / "linked")
        with pytest.raises(CatalogSnapshotError, match="symlink"):
            capture_catalog(tmp_path)
    finally:
        outside.unlink()


def test_snapshot_rejects_excess_file_bytes(tmp_path, monkeypatch):
    _write(tmp_path, "big.yml", "item: 123456789\n")
    monkeypatch.setattr("tools.catalog_snapshot.MAX_FILE_BYTES", 4)
    with pytest.raises(CatalogSnapshotError, match="file limit"):
        capture_catalog(tmp_path)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="named pipes unavailable")
def test_snapshot_rejects_named_pipe_without_blocking(tmp_path):
    os.mkfifo(tmp_path / "pipe.yml")
    with pytest.raises(CatalogSnapshotError, match="not a regular file"):
        capture_catalog(tmp_path)

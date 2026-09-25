"""The source snapshot records exact bytes without confusing YAML semantics."""

import os
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.catalog_snapshot import (CatalogSnapshotError, capture_catalog,
                                    capture_catalog_revision)
from tools.catalog_candidate import compare_catalog_revisions


def _write(root, name, contents):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    return path


def _git(root, *args):
    result = subprocess.run(["git", "-C", str(root)] + list(args), check=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.stdout.decode("ascii").strip()


def _committed_catalog(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Catalog Test")
    _git(repo, "config", "user.email", "catalog-test@example.invalid")
    _write(repo, "config/item.yml", "value: 1\n")
    _git(repo, "add", "config")
    _git(repo, "commit", "-qm", "initial catalog")
    return repo, _git(repo, "rev-parse", "HEAD")


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


def test_git_snapshot_is_bound_to_exact_committed_bytes(tmp_path):
    repo, commit = _committed_catalog(tmp_path)
    accepted = capture_catalog_revision(repo, commit)
    assert accepted["git"]["commit"] == commit
    assert accepted["git"]["source_root"] == "config"
    assert len(accepted["git"]["tree"]) == len(commit)
    assert accepted["source_sha256"] == capture_catalog(repo / "config")["source_sha256"]
    _write(repo, "config/item.yml", "value: 2\n")
    _write(repo, "config/new.yml", "value: 3\n")
    assert capture_catalog_revision(repo, commit) == accepted
    assert capture_catalog(repo / "config")["source_sha256"] != accepted["source_sha256"]
    _git(repo, "add", "config")
    _git(repo, "commit", "-qm", "update catalog")
    later = capture_catalog_revision(repo, _git(repo, "rev-parse", "HEAD"))
    assert later["file_count"] == 2
    assert later["source_sha256"] != accepted["source_sha256"]


def test_git_snapshot_ignores_local_replace_refs(tmp_path):
    repo, commit = _committed_catalog(tmp_path)
    accepted = capture_catalog_revision(repo, commit)
    _write(repo, "config/item.yml", "value: replacement\n")
    _git(repo, "add", "config")
    _git(repo, "commit", "-qm", "other catalog")
    _git(repo, "replace", commit, _git(repo, "rev-parse", "HEAD"))
    assert capture_catalog_revision(repo, commit) == accepted


def test_git_snapshot_cli_emits_bound_manifest(tmp_path):
    repo, commit = _committed_catalog(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "tools.catalog_snapshot", "config", "--repo",
         str(repo), "--revision", commit],
        cwd=Path(__file__).resolve().parents[1], check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    manifest = json.loads(result.stdout)
    assert manifest == capture_catalog_revision(repo, commit)


def test_git_snapshot_rejects_non_exact_revision_and_bad_root(tmp_path):
    repo, commit = _committed_catalog(tmp_path)
    with pytest.raises(CatalogSnapshotError, match="exact Git commit"):
        capture_catalog_revision(repo, "HEAD")
    with pytest.raises(CatalogSnapshotError, match="exact Git commit"):
        capture_catalog_revision(repo, commit[:10])
    with pytest.raises(CatalogSnapshotError, match="safe repository-relative"):
        capture_catalog_revision(repo, commit, "../config")
    with pytest.raises(CatalogSnapshotError, match="Git rev-parse failed"):
        capture_catalog_revision(repo, commit, "missing")


def test_git_snapshot_rejects_committed_symlink(tmp_path):
    repo, _ = _committed_catalog(tmp_path)
    os.symlink("item.yml", repo / "config" / "linked.yml")
    _git(repo, "add", "config")
    _git(repo, "commit", "-qm", "add link")
    with pytest.raises(CatalogSnapshotError, match="symlink"):
        capture_catalog_revision(repo, _git(repo, "rev-parse", "HEAD"))


def test_git_snapshot_rejects_oversize_blob_before_read(tmp_path, monkeypatch):
    repo, commit = _committed_catalog(tmp_path)
    monkeypatch.setattr("tools.catalog_snapshot.MAX_FILE_BYTES", 4)
    with pytest.raises(CatalogSnapshotError, match="file limit"):
        capture_catalog_revision(repo, commit)


def test_git_snapshot_rejects_unsafe_source_path(tmp_path):
    repo, _ = _committed_catalog(tmp_path)
    _write(repo, "config/bad\nname.yml", "value: 1\n")
    _git(repo, "add", "config")
    _git(repo, "commit", "-qm", "add unsafe name")
    with pytest.raises(CatalogSnapshotError, match="unsafe catalog source path"):
        capture_catalog_revision(repo, _git(repo, "rev-parse", "HEAD"))


def test_catalog_path_byte_limit_applies_to_git_and_worktree(tmp_path, monkeypatch):
    repo, commit = _committed_catalog(tmp_path)
    monkeypatch.setattr("tools.catalog_snapshot.MAX_PATH_BYTES", 7)
    with pytest.raises(CatalogSnapshotError, match="unsafe catalog source path"):
        capture_catalog(repo / "config")
    with pytest.raises(CatalogSnapshotError, match="unsafe catalog source path"):
        capture_catalog_revision(repo, commit)


def test_semantic_alias_expansion_has_a_budget(tmp_path, monkeypatch):
    _write(tmp_path, "alias.yml", "a: &a [1, 2]\nb: [*a, *a, *a]\n")
    monkeypatch.setattr("tools.catalog_snapshot.MAX_SEMANTIC_NODES", 8)
    with pytest.raises(CatalogSnapshotError, match="semantic node limit"):
        capture_catalog(tmp_path)


def test_catalog_semantic_budget_bounds_many_files(tmp_path, monkeypatch):
    _write(tmp_path, "a.yml", "value: 1\n")
    _write(tmp_path, "b.yml", "value: 2\n")
    monkeypatch.setattr("tools.catalog_snapshot.MAX_TOTAL_SEMANTIC_NODES", 5)
    with pytest.raises(CatalogSnapshotError, match="catalog exceeds semantic node limit"):
        capture_catalog(tmp_path)


def test_candidate_classifies_exact_source_changes_without_accepting_them(tmp_path):
    repo, _ = _committed_catalog(tmp_path)
    _write(repo, "config/format.yml", "value: {b: 2, a: 1}\n")
    _write(repo, "config/removed.yml", "value: gone\n")
    _git(repo, "add", "config")
    _git(repo, "commit", "-qm", "baseline")
    base = _git(repo, "rev-parse", "HEAD")
    _write(repo, "config/item.yml", "value: 2\n")
    _write(repo, "config/format.yml", "# comment\nvalue: {a: 1, b: 2}\n")
    (repo / "config" / "removed.yml").unlink()
    _write(repo, "config/added.yml", "value: new\n")
    _git(repo, "add", "-A", "config")
    _git(repo, "commit", "-qm", "candidate")
    candidate = _git(repo, "rev-parse", "HEAD")
    report = compare_catalog_revisions(repo, base, candidate)
    assert report == compare_catalog_revisions(repo, base, candidate)
    assert report["state"] == "candidate"
    assert report["validation"] == {"source": "passed", "domain": "not_run"}
    assert report["summary"] == {"added": 1, "removed": 1,
                                 "semantic_changed": 1, "formatting_only": 1,
                                 "unchanged": 0}
    assert [(change["path"], change["kind"]) for change in report["changes"]] == [
        ("added.yml", "added"), ("format.yml", "formatting_only"),
        ("item.yml", "semantic_changed"), ("removed.yml", "removed")]
    assert [finding["code"] for finding in report["findings"]] == [
        "source_removed", "domain_validation_pending"]
    assert report["base"]["commit"] == base
    assert report["candidate"]["commit"] == candidate
    result = subprocess.run(
        [sys.executable, "-m", "tools.catalog_candidate", "config", "--repo",
         str(repo), "--base", base, "--candidate", candidate],
        cwd=Path(__file__).resolve().parents[1], check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert json.loads(result.stdout) == report


def test_candidate_does_not_infer_rename_or_withdrawal(tmp_path):
    repo, base = _committed_catalog(tmp_path)
    (repo / "config" / "item.yml").rename(repo / "config" / "renamed.yml")
    _git(repo, "add", "-A", "config")
    _git(repo, "commit", "-qm", "rename source path")
    report = compare_catalog_revisions(repo, base, _git(repo, "rev-parse", "HEAD"))
    assert [(change["path"], change["kind"]) for change in report["changes"]] == [
        ("item.yml", "removed"), ("renamed.yml", "added")]
    assert report["findings"][0]["code"] == "source_removed"


def test_invalid_candidate_is_a_failed_source_report(tmp_path):
    repo, base = _committed_catalog(tmp_path)
    _write(repo, "config/item.yml", "value: 1\nvalue: 2\n")
    _git(repo, "add", "config")
    _git(repo, "commit", "-qm", "invalid YAML")
    candidate = _git(repo, "rev-parse", "HEAD")
    report = compare_catalog_revisions(repo, base, candidate)
    assert report["state"] == "invalid_source"
    assert report["candidate"] is None
    assert report["summary"] is None
    assert report["changes"] == []
    assert report["validation"] == {"source": "failed", "domain": "not_run"}
    assert report["findings"][0]["code"] == "invalid_source"
    assert "duplicate key" in report["findings"][0]["detail"]
    result = subprocess.run(
        [sys.executable, "-m", "tools.catalog_candidate", "config", "--repo",
         str(repo), "--base", base, "--candidate", candidate],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert result.returncode == 2
    assert json.loads(result.stdout) == report


def test_unchanged_candidate_still_requires_domain_validation(tmp_path):
    repo, commit = _committed_catalog(tmp_path)
    report = compare_catalog_revisions(repo, commit, commit)
    assert report["summary"]["unchanged"] == 1
    assert report["changes"] == []
    assert report["findings"][0]["code"] == "domain_validation_pending"


def test_opt_in_domain_validation_reports_scope_and_fails_invalid_candidate(tmp_path):
    repo, commit = _committed_catalog(tmp_path)
    report = compare_catalog_revisions(repo, commit, commit, validate_domain=True)
    assert report["state"] == "domain_invalid"
    assert report["validation"] == {"source": "passed", "domain": "failed"}
    assert report["domain_report"]["scope"] == "identity-and-direct-references/v1"
    assert any(row["code"] == "missing_kind" for row in report["domain_report"]["findings"])
    assert report["findings"] == []
    result = subprocess.run(
        [sys.executable, "-m", "tools.catalog_candidate", "config", "--repo",
         str(repo), "--base", commit, "--candidate", commit,
         "--validate-domain"], cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert result.returncode == 2
    assert json.loads(result.stdout) == report


def test_domain_pass_uses_exact_committed_documents(tmp_path):
    repo, _ = _committed_catalog(tmp_path)
    (repo / "config" / "item.yml").unlink()
    _write(repo, "config/toolchains.yml", "Toolchains: [{Id: tool}]\n")
    _write(repo, "config/chips/vendor/family.yml", "Chips: [{Id: chip}]\n")
    _write(repo, "config/boards/vendor/family.yml",
           "Boards: [{Id: board, Chip: chip}]\n")
    _write(repo, "config/capabilities/leds.yml", "Capability: {id: leds}\n")
    _write(repo, "config/peripherals/led.yml",
           "Peripheral: {id: led, provides: [{capability: leds}]}\n")
    _write(repo, "config/configurations/rig.yml",
           "Configuration: {id: rig, board: board, toolchain: tool, "
           "attach: [{peripheral: led}]}\n")
    _git(repo, "add", "-A", "config")
    _git(repo, "commit", "-qm", "valid domain subset")
    commit = _git(repo, "rev-parse", "HEAD")
    report = compare_catalog_revisions(repo, commit, commit, validate_domain=True)
    assert report["state"] == "candidate"
    assert report["validation"]["domain"] == "partial_passed"
    assert report["domain_report"]["findings"] == []
    assert report["domain_report"]["unexamined"] == []
    _write(repo, "config/configurations/rig.yml",
           "Configuration: {id: rig, board: wrong, toolchain: tool}\n")
    assert compare_catalog_revisions(repo, commit, commit, validate_domain=True) == report

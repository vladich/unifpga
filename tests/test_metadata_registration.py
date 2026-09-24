"""Catalog registration must not silently discard or overwrite YAML records."""

import pytest

from config import init as config_init
from tools import setup


def _write(directory, name, contents):
    path = directory / name
    path.write_text(contents, encoding="utf-8")
    return path


def test_directory_loader_rejects_duplicate_ids(tmp_path):
    first = _write(tmp_path, "first.yml", "Peripheral: {id: duplicate}\n")
    second = _write(tmp_path, "second.yml", "Peripheral: {id: duplicate}\n")
    with pytest.raises(config_init.ConfigError, match="duplicate Peripheral 'duplicate'") as exc:
        config_init._load_yaml_dir("peripherals", "Peripheral", "id", base=tmp_path)
    assert str(first) in str(exc.value) and str(second) in str(exc.value)


@pytest.mark.parametrize("contents", [
    "Other: {id: item}\n",
    "Peripheral: []\n",
    "Peripheral: {name: item}\n",
    "Peripheral: {id: false}\n",
    "Peripheral: {id: '   '}\n",
])
def test_directory_loader_rejects_malformed_record(tmp_path, contents):
    path = _write(tmp_path, "bad.yml", contents)
    with pytest.raises(config_init.ConfigError, match="bad.yml"):
        config_init._load_yaml_dir("peripherals", "Peripheral", "id", base=tmp_path)
    assert path.exists()


def test_duplicate_yaml_mapping_key_rejected_before_overwrite(tmp_path):
    path = _write(tmp_path, "bad.yml", "Peripheral:\n  id: first\n  id: second\n")
    with pytest.raises(config_init.ConfigError, match="duplicate key 'id'"):
        config_init._read_yaml_file(str(path))


def test_yaml_merge_may_override_inherited_default(tmp_path):
    path = _write(tmp_path, "merged.yml", "defaults: &defaults {id: inherited, value: 1}\n"
                  "Peripheral:\n  <<: *defaults\n  id: explicit\n")
    assert config_init._read_yaml_file(str(path))["Peripheral"] == {
        "id": "explicit", "value": 1}


def test_setups_use_the_same_duplicate_detection(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "SETUP_DIR", str(tmp_path))
    _write(tmp_path, "a.yml", "Setup: {id: repeated}\n")
    _write(tmp_path, "b.yml", "Setup: {id: repeated}\n")
    with pytest.raises(config_init.ConfigError, match="duplicate Setup 'repeated'"):
        setup.read_setups()


def test_missing_setup_directory_still_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "SETUP_DIR", str(tmp_path / "absent"))
    assert setup.read_setups() == {}


def test_single_file_registry_rejects_duplicate_ids(tmp_path, monkeypatch):
    _write(tmp_path, "features.yml", "Features:\n  - Id: repeated\n  - Id: repeated\n")
    monkeypatch.setattr(config_init, "dir_path", str(tmp_path))
    with pytest.raises(config_init.ConfigError, match="duplicate Feature Id 'repeated'"):
        config_init.read_features()


def test_single_file_registry_rejects_blank_id(tmp_path, monkeypatch):
    _write(tmp_path, "features.yml", "Features:\n  - Id: '  '\n")
    monkeypatch.setattr(config_init, "dir_path", str(tmp_path))
    with pytest.raises(config_init.ConfigError, match="nonempty string Id"):
        config_init.read_features()


@pytest.mark.parametrize("kind, path, list_name, reader", [
    ("board", "boards", "Boards", config_init.read_boards_catalog),
    ("chip", "chips", "Chips", config_init.read_chips),
])
def test_family_registries_reject_duplicate_ids(tmp_path, monkeypatch,
                                                 kind, path, list_name, reader):
    family = tmp_path / path / "lattice"
    family.mkdir(parents=True)
    for name in ("a.yml", "b.yml"):
        _write(family, name, "Producer: Lattice\nFamily: Test\n{}:\n  - Id: repeated\n"
               .format(list_name))
    monkeypatch.setattr(config_init, "dir_path", str(tmp_path))
    with pytest.raises(config_init.ConfigError, match="duplicate {} Id 'repeated'".format(kind)):
        reader()


@pytest.mark.parametrize("path, list_name, reader", [
    ("boards", "Boards", config_init.read_boards_catalog),
    ("chips", "Chips", config_init.read_chips),
])
def test_family_registries_reject_null_list(tmp_path, monkeypatch,
                                            path, list_name, reader):
    family = tmp_path / path / "lattice"
    family.mkdir(parents=True)
    _write(family, "bad.yml", "Producer: Lattice\nFamily: Test\n{}: null\n"
           .format(list_name))
    monkeypatch.setattr(config_init, "dir_path", str(tmp_path))
    with pytest.raises(config_init.ConfigError, match="{} must be a list".format(list_name)):
        reader()


@pytest.mark.parametrize("path, list_name, reader", [
    ("boards", "Boards", config_init.read_boards_catalog),
    ("chips", "Chips", config_init.read_chips),
])
def test_family_registries_require_identity_and_list(tmp_path, monkeypatch,
                                                     path, list_name, reader):
    family = tmp_path / path / "lattice"
    family.mkdir(parents=True)
    catalog = _write(family, "bad.yml", "Producer: Lattice\nFamily: Test\n")
    monkeypatch.setattr(config_init, "dir_path", str(tmp_path))
    with pytest.raises(config_init.ConfigError, match="{} must be a list".format(list_name)):
        reader()
    catalog.write_text("{}: []\n".format(list_name), encoding="utf-8")
    with pytest.raises(config_init.ConfigError, match="needs Producer and Family"):
        reader()


def test_tinyfpga_bx_uses_lp_hx_family_and_keeps_pinmap():
    board = config_init.read_board_entry("tinyfpga_bx")
    chip = config_init.read_chips()[board["Chip"]]
    pinmap = config_init.read_board_pinmap("tinyfpga_bx")
    assert board["PartFamily"] == chip["PartFamily"] == "ICE40"
    assert board["_family_dir"] == "ice40"
    assert pinmap["id"] == "tinyfpga_bx"

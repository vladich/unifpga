"""Version admission from chip requirements and resolved installations."""

import pytest

import config.init
import program
import synthesize
from tools import toolchain_detect


@pytest.mark.parametrize("version,constraint,expected", [
    ("2024.1", "*", True),
    (None, "*", True),
    ("2023.2", "2024.1+", False),
    ("2024.1", "2024.1+", True),
    ("2024.2", "2024.1+", True),
    (None, "2024.1+", False),
    ("23.1std", "18.1-23.1std", True),
    ("24.1std", "18.1-23.1std", False),
    ("git-master", "git-master", True),
    ("2024.1", "git-master", False),
])
def test_chip_version_constraints(version, constraint, expected):
    assert toolchain_detect.matches_version(version, constraint) is expected


@pytest.mark.parametrize("constraint", ["", "2024.1++", "2024.2-2024.1"])
def test_malformed_version_constraints_are_rejected(constraint):
    with pytest.raises(ValueError):
        toolchain_detect.matches_version("2024.1", constraint)


def test_spartan_ultrascale_plus_requirement_is_retained():
    chips = config.init.read_chips()
    boards = {"example": {"Chip": "XCSU35P-2SBVB625I"}}
    assert config.init.toolchain_constraints(boards, chips, "example", "vivado") == ["2024.1+"]
    assert config.init.toolchain_constraints(boards, chips, "example", "quartus2") == []


def test_version_requirement_uses_selected_board_chip(monkeypatch):
    chips = config.init.read_chips()
    selected = "xc7a100tcsg324-1"
    chips[selected]["Toolchains"] = ["vivado[2024.1+]"]
    monkeypatch.setattr(config.init, "read_chips", lambda: chips)
    resolved = config.init.resolve_configuration("nexys_a7_100")
    resolved["toolchain"]["DetectSource"] = None
    assert resolved["toolchain"]["ChipVersionConstraints"] == ["2024.1+"]
    with pytest.raises(config.init.ConfigError, match="does not meet chip constraint"):
        config.init.require_toolchain_version(resolved["toolchain"])

    other = config.init.resolve_configuration("nexys_a7_50")
    other["toolchain"]["DetectSource"] = None
    assert other["toolchain"]["ChipVersionConstraints"] == ["*"]
    config.init.require_toolchain_version(other["toolchain"])


def test_detected_version_must_agree_with_configured_version(monkeypatch):
    monkeypatch.setattr(toolchain_detect, "detect", lambda tid, pin=None:
                        toolchain_detect._found(tid, "vendor-install", [], {}, "test", "2024.1", []))
    tc = config.init.resolve_toolchain_install({"Id": "vivado", "Version": "2023.2"})
    tc["ChipVersionConstraints"] = ["*"]
    assert tc["ConfiguredVersion"] == "2023.2" and tc["DetectedInstallVersion"] == "2024.1"
    with pytest.raises(config.init.ConfigError, match="conflicts with configured version"):
        config.init.require_toolchain_version(tc)


def test_detected_version_is_checked_against_chip_requirement(monkeypatch):
    monkeypatch.setattr(toolchain_detect, "detect", lambda tid, pin=None:
                        toolchain_detect._found(tid, "vendor-install", [], {}, "test", "2023.2", []))
    tc = config.init.resolve_toolchain_install({"Id": "vivado", "Version": None})
    tc["ChipVersionConstraints"] = ["2024.1+"]
    with pytest.raises(config.init.ConfigError, match="does not meet chip constraint"):
        config.init.require_toolchain_version(tc)
    tc["DetectedInstallVersion"] = "2024.1"
    config.init.require_toolchain_version(tc)


def test_unknown_detected_version_does_not_inherit_configured_pin(monkeypatch):
    monkeypatch.setattr(toolchain_detect, "detect", lambda tid, pin=None:
                        toolchain_detect._found(tid, "vendor-install", [], {}, "test", None, []))
    tc = config.init.resolve_toolchain_install({"Id": "vivado", "Version": "2024.1"})
    tc["ChipVersionConstraints"] = ["2024.1+"]
    with pytest.raises(config.init.ConfigError, match="could not be determined"):
        config.init.require_toolchain_version(tc)


def test_invalid_chip_variant_constraint_cannot_hide_behind_wildcard():
    tc = {"Id": "vivado", "ChipVersionConstraints": ["*", "2024.2-2024.1"]}
    with pytest.raises(config.init.ConfigError, match="invalid chip version constraint"):
        config.init.require_toolchain_version(tc)


def test_synthesis_rejects_version_conflict_before_output(tmp_path, monkeypatch):
    resolved = config.init.resolve_configuration("tang_nano_9k_hdmi_tm1638")
    resolved["toolchain"]["DetectedInstallVersion"] = "wrong-version"
    resolved["toolchain"]["DetectSource"] = "test"
    monkeypatch.setattr(config.init, "read_or_init", lambda _: resolved)
    monkeypatch.setattr(synthesize, "prepare_toolchain", lambda _: pytest.fail("tool setup ran"))
    output = tmp_path / "new-build"
    assert synthesize.main(["--top", "design_top.sv", "-o", str(output)]) == 2
    assert not output.exists()


def test_standalone_program_rejects_version_conflict(tmp_path, monkeypatch):
    resolved = config.init.resolve_configuration("tang_nano_9k_hdmi_tm1638")
    resolved["toolchain"]["DetectedInstallVersion"] = "wrong-version"
    resolved["toolchain"]["DetectSource"] = "test"
    monkeypatch.setattr(config.init, "read_or_init", lambda _: resolved)
    monkeypatch.setattr(synthesize, "toolchain_module", lambda _: pytest.fail("driver loaded"))
    assert program.main(["-o", str(tmp_path)]) == 2

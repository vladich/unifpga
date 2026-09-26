"""Physical-build admission keeps unreviewed board data out of hardware jobs."""

import copy

import pytest

import config.init
import program
import synthesize


def _verified(resolved):
    resolved = copy.deepcopy(resolved)
    board = resolved["board"]
    board["verification"] = {
        "status": "verified",
        "banks_sha256": config.init.board_fingerprint(board),
        "parts": [board["chip_id"]],
        "pinout": {"source": "vendor board schematic", "revision": "rev A"},
        "electrical": {"source": "vendor electrical manual", "revision": "rev B"},
    }
    return resolved


def test_unverified_and_placeholder_boards_are_rejected():
    unverified = config.init.resolve_configuration("tang_nano_9k_hdmi_tm1638")
    placeholder = config.init.resolve_configuration("gatemate_evb_a1")
    with pytest.raises(config.init.ConfigError, match="not verified for hardware"):
        config.init.require_hardware_readiness(unverified["board"])
    with pytest.raises(config.init.ConfigError, match="not verified for hardware"):
        config.init.require_hardware_readiness(placeholder["board"])


def test_exact_banks_and_part_attestation_is_required():
    resolved = _verified(config.init.resolve_configuration("tang_nano_9k_hdmi_tm1638"))
    board = resolved["board"]
    config.init.require_hardware_readiness(board)

    changed = copy.deepcopy(board)
    changed["banks"]["clk"]["pins"] = "different-pin"
    with pytest.raises(config.init.ConfigError, match="digest is missing or stale"):
        config.init.require_hardware_readiness(changed)

    wrong_part = copy.deepcopy(board)
    wrong_part["chip_id"] = "different-part"
    with pytest.raises(config.init.ConfigError, match="does not cover selected part"):
        config.init.require_hardware_readiness(wrong_part)

    no_electrical = copy.deepcopy(board)
    no_electrical["verification"]["electrical"]["revision"] = ""
    with pytest.raises(config.init.ConfigError, match="lacks electrical source and revision"):
        config.init.require_hardware_readiness(no_electrical)

    other_board = copy.deepcopy(board)
    other_board["id"] = "another-board"                      # the digest covers the board's identity
    with pytest.raises(config.init.ConfigError, match="digest is missing or stale"):
        config.init.require_hardware_readiness(other_board)


def test_physical_synthesis_rejects_an_unverified_board_before_tool_setup(tmp_path, monkeypatch):
    resolved = config.init.resolve_configuration("tang_nano_9k_hdmi_tm1638")
    monkeypatch.setattr(config.init, "read_or_init", lambda _, **__: resolved)
    monkeypatch.setattr(synthesize, "prepare_toolchain", lambda _: pytest.fail("tool setup ran"))
    output = tmp_path / "new-build"
    assert synthesize.main(["--top", "design_top.sv", "-o", str(output)]) == 2
    assert not output.exists()


def test_physical_program_rejects_an_unverified_board_before_driver(tmp_path, monkeypatch):
    resolved = config.init.resolve_configuration("tang_nano_9k_hdmi_tm1638")
    monkeypatch.setattr(config.init, "read_or_init", lambda _: resolved)
    monkeypatch.setattr(synthesize, "toolchain_module", lambda _: pytest.fail("driver loaded"))
    assert program.main(["-o", str(tmp_path)]) == 2


def test_verified_board_admits_program_driver(tmp_path, monkeypatch):
    resolved = _verified(config.init.resolve_configuration("tang_nano_9k_hdmi_tm1638"))
    calls = []
    monkeypatch.setattr(config.init, "read_or_init", lambda _: resolved)
    monkeypatch.setattr(synthesize, "prepare_toolchain", lambda _: None)

    class Driver:
        @staticmethod
        def program(**kwargs):
            calls.append(kwargs)
            return 0

    monkeypatch.setattr(synthesize, "toolchain_module", lambda _: Driver)
    assert program.main(["-o", str(tmp_path)]) == 0
    assert len(calls) == 1 and calls[0]["board"] is resolved["board"]


@pytest.mark.parametrize("result", [None, True, "0"])
def test_driver_must_return_an_explicit_integer_status(result):
    assert synthesize.driver_exit_code(result, "program") == 2


def test_program_does_not_turn_missing_driver_status_into_success(tmp_path, monkeypatch):
    resolved = _verified(config.init.resolve_configuration("tang_nano_9k_hdmi_tm1638"))
    monkeypatch.setattr(config.init, "read_or_init", lambda _: resolved)
    monkeypatch.setattr(synthesize, "prepare_toolchain", lambda _: None)

    class Driver:
        @staticmethod
        def program(**kwargs):
            return None

    monkeypatch.setattr(synthesize, "toolchain_module", lambda _: Driver)
    assert program.main(["-o", str(tmp_path)]) == 2

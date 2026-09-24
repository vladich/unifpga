"""
config/profiles/<id>.yml — the design-wiring conventions live apart from the
hardware configuration and are applied on top of it (config/profile.py).
"""

import os
import sys

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from config import init as config_init     # noqa: E402
from config import profile                 # noqa: E402
from tools import codegen                  # noqa: E402

CFG = "tang_nano_9k_hdmi_tm1638"
GENERIC = os.path.join(REPO, "config", "configurations", CFG + ".yml")


def _lines(text, needle):
    return [l.strip() for l in text.splitlines() if needle in l]


def test_configuration_carries_no_profile_convention():
    """No reset policy, lab_bits, as_switches, mirror, lab_clock or reset-driven
    tie in any configuration: those belong to the profile, not the board."""
    cfg_dir = os.path.join(REPO, "config", "configurations")
    for name in sorted(os.listdir(cfg_dir)):
        cfg = yaml.safe_load(open(os.path.join(cfg_dir, name)))["Configuration"]
        assert "reset" not in cfg and "lab_clock" not in cfg, name
        for a in cfg.get("attach") or []:
            assert "lab_bits" not in a, (name, a.get("peripheral"))
            assert not set(a.get("params") or {}) & set(profile.PROFILE_PARAMS), (name, a.get("peripheral"))
        for ref, v in (cfg.get("tie") or {}).items():
            assert "rst" not in str(v), (name, ref)


def test_profile_is_applied_by_default_and_can_be_switched_off(monkeypatch):
    monkeypatch.delenv("UNIFPGA_PROFILE", raising=False)
    config_init.clear_cache()
    r = config_init.resolve_configuration(CFG)
    text = codegen.emit_top_sv(r)
    assert ".w_btn(8)," in text                                  # the TM1638 is the design's key bus
    assert _lines(text, "assign rst =") == ["assign rst = rst_on_power_up | ((~ onboard_buttons[0]) | (~ onboard_buttons[1]));"]
    tm = next(a for a in r["peripherals"] if a["peripheral_id"] == "tm1638_led_key")
    assert tm["lab_bits"]["buttons"] == list(range(8))

    monkeypatch.setenv("UNIFPGA_PROFILE", "0")
    config_init.clear_cache()
    r = config_init.resolve_configuration(CFG)
    text = codegen.emit_top_sv(r)
    assert ".w_btn(10)," in text                                 # generic: concatenation in attach order
    assert _lines(text, "assign rst =") == ["assign rst = rst_on_power_up;"]
    assert not any(a.get("lab_bits") for a in r["peripherals"])
    config_init.clear_cache()


def test_profile_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(profile, "PROFILE_DIR", str(tmp_path))
    assert profile.load("x") is None
    assert profile.update("x", reset={"sources": [{"any_key": True}]})
    assert profile.set_attach("x", "tm1638_led_key", 0, lab_bits={"buttons": list(range(8))}, params={"mirror": True})
    data = profile.load("x")
    assert data["reset"] == {"sources": [{"any_key": True}]} and "variant" not in data
    e = profile.attach_override(data, "tm1638_led_key", 0, create=False)
    assert e["lab_bits"] == {"buttons": list(range(8))} and e["params"] == {"mirror": True}
    assert not profile.update("x", reset={"sources": [{"any_key": True}]})   # idempotent
    # removing everything removes the file
    profile.set_attach("x", "tm1638_led_key", 0, lab_bits={}, params={"mirror": None})
    assert profile.update("x", reset=None) and profile.load("x") is None


def test_profiles_hold_only_profile_fields():
    """Every profile names its own configuration and carries nothing but the
    profile vocabulary."""
    pdir = os.path.join(REPO, "config", "profiles")
    for name in sorted(os.listdir(pdir)):
        data = yaml.safe_load(open(os.path.join(pdir, name)))
        assert list(data) == ["Profile"], name
        body = data["Profile"]
        assert body["configuration"] == name[:-4], name
        assert set(body) - {"configuration"} <= set(profile.TOP_KEYS) | {"for_toolchain"}, name
        for patch in (body.get("for_toolchain") or {}).values():
            assert set(patch) <= set(profile.TOP_KEYS), name
        assert os.path.exists(os.path.join(REPO, "config", "configurations", name)), name

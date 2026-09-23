"""
config/bgm/<id>.yml — BGM's lab conventions live apart from the generic
configuration and are applied on top of it (tools/bgm_overlay.py).
"""

import os
import sys

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from config import init as config_init     # noqa: E402
from tools import bgm_overlay, codegen     # noqa: E402

CFG = "tang_nano_9k_hdmi_tm1638"
GENERIC = os.path.join(REPO, "config", "configurations", CFG + ".yml")


def _lines(text, needle):
    return [l.strip() for l in text.splitlines() if needle in l]


def test_generic_configuration_carries_no_bgm_convention():
    """No reset policy, lab_bits, as_switches, mirror, lab_clock or reset-driven
    tie in any generic configuration: those are BGM's, not the board's."""
    cfg_dir = os.path.join(REPO, "config", "configurations")
    for name in sorted(os.listdir(cfg_dir)):
        cfg = yaml.safe_load(open(os.path.join(cfg_dir, name)))["Configuration"]
        assert "reset" not in cfg and "lab_clock" not in cfg, name
        for a in cfg.get("attach") or []:
            assert "lab_bits" not in a, (name, a.get("peripheral"))
            assert not set(a.get("params") or {}) & set(bgm_overlay.OVERLAY_PARAMS), (name, a.get("peripheral"))
        for ref, v in (cfg.get("tie") or {}).items():
            assert "rst" not in str(v), (name, ref)


def test_overlay_is_applied_by_default_and_can_be_switched_off(monkeypatch):
    monkeypatch.delenv("UNIFPGA_BGM_OVERLAY", raising=False)
    config_init.clear_cache()
    r = config_init.resolve_configuration(CFG)
    text = codegen.emit_top_sv(r)
    assert ".w_btn(8)," in text                                  # the TM1638 is the lab's key bus
    assert _lines(text, "assign rst =") == ["assign rst = rst_on_power_up | ((~ onboard_buttons[0]) | (~ onboard_buttons[1]));"]
    tm = next(a for a in r["peripherals"] if a["peripheral_id"] == "tm1638_led_key")
    assert tm["lab_bits"]["buttons"] == list(range(8))

    monkeypatch.setenv("UNIFPGA_BGM_OVERLAY", "0")
    config_init.clear_cache()
    r = config_init.resolve_configuration(CFG)
    text = codegen.emit_top_sv(r)
    assert ".w_btn(10)," in text                                 # generic: concatenation in attach order
    assert _lines(text, "assign rst =") == ["assign rst = rst_on_power_up;"]
    assert not any(a.get("lab_bits") for a in r["peripherals"])
    config_init.clear_cache()


def test_overlay_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(bgm_overlay, "OVERLAY_DIR", str(tmp_path))
    assert bgm_overlay.load("x") is None
    assert bgm_overlay.update("x", "x_variant", reset={"sources": [{"any_key": True}]})
    assert bgm_overlay.set_attach("x", "tm1638_led_key", 0, lab_bits={"buttons": list(range(8))}, params={"mirror": True})
    data = bgm_overlay.load("x")
    assert data["variant"] == "x_variant" and data["reset"] == {"sources": [{"any_key": True}]}
    e = bgm_overlay.attach_override(data, "tm1638_led_key", 0, create=False)
    assert e["lab_bits"] == {"buttons": list(range(8))} and e["params"] == {"mirror": True}
    assert not bgm_overlay.update("x", "x_variant", reset={"sources": [{"any_key": True}]})   # idempotent
    # removing everything removes the file
    bgm_overlay.set_attach("x", "tm1638_led_key", 0, lab_bits={}, params={"mirror": None})
    assert bgm_overlay.update("x", reset=None) and bgm_overlay.load("x") is None

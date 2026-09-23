"""
tools/bgm_overlay.py -- the BGM-sync view of unifpga's design-wiring profiles.

The profiles themselves (config/profiles/<id>.yml) are unifpga's, handled by
config/profile.py. The BGM tooling additionally records, per configuration,
the BGM variant it was synced from and the upstream bugs it found and did not
reproduce; those live in bgm_extras.yml next to this file, never in unifpga's
data. The functions keep the signatures the sync tools use (variant=).
"""

import os
from collections import OrderedDict

import yaml

from config import profile as _profile
from config.profile import attach_override, enabled, apply, REMOVE, PROFILE_PARAMS as OVERLAY_PARAMS  # noqa: F401

EXTRAS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bgm_extras.yml")


def __getattr__(name):
    # KEEP_DROPPED is read and set by the sync tools; it lives on config.profile
    if name == "KEEP_DROPPED":
        return _profile.KEEP_DROPPED
    raise AttributeError(name)


def set_keep_dropped(value):
    _profile.KEEP_DROPPED = value


def _extras():
    if not os.path.exists(EXTRAS):
        return {}
    return yaml.safe_load(open(EXTRAS, encoding="utf-8")) or {}


def _save_extras(data):
    data = {k: v for k, v in sorted(data.items()) if v}
    text = yaml.safe_dump(data, sort_keys=True, width=100, allow_unicode=True)
    old = open(EXTRAS, encoding="utf-8").read() if os.path.exists(EXTRAS) else None
    if old != text:
        with open(EXTRAS, "w", encoding="utf-8") as f:
            f.write(text)


def _note_variant(configuration_id, variant):
    if variant:
        data = _extras()
        e = data.setdefault(configuration_id, {})
        if e.get("variant") != variant:
            e["variant"] = variant
            _save_extras(data)


def load(configuration_id):
    """The profile plus the BGM-only extras (variant, bgm_bugs)."""
    data = _profile.load(configuration_id)
    extra = _extras().get(configuration_id) or {}
    if data is None and not extra.get("bgm_bugs"):
        return None
    data = OrderedDict(data or {})
    for k in ("variant", "bgm_bugs"):
        if extra.get(k):
            data[k] = extra[k]
    return data


def save(configuration_id, data, variant=None):
    data = dict(data or {})
    _note_variant(configuration_id, data.pop("variant", None) or variant)
    bugs = data.pop("bgm_bugs", None)
    ex = _extras()
    e = ex.setdefault(configuration_id, {})
    if (bugs or None) != (e.get("bgm_bugs") or None):
        if bugs:
            e["bgm_bugs"] = dict(bugs)
        else:
            e.pop("bgm_bugs", None)
        _save_extras(ex)
    return _profile.save(configuration_id, data)


def update(configuration_id, variant=None, **fields):
    _note_variant(configuration_id, variant)
    if "bgm_bugs" in fields:
        raise ValueError("use set_bug() for bgm_bugs")
    return _profile.update(configuration_id, **fields)


def set_attach(configuration_id, peripheral, index, lab_bits=None, params=None, variant=None, clear_lab_bits=False,
               drop=None, bind=None):
    _note_variant(configuration_id, variant)
    return _profile.set_attach(configuration_id, peripheral, index, lab_bits=lab_bits, params=params,
                               clear_lab_bits=clear_lab_bits, drop=drop, bind=bind)


def set_bug(configuration_id, variant, key, description):
    """Record (description) or clear (None) one upstream bug found in BGM's
    source and not reproduced. Returns True when the record changed."""
    _note_variant(configuration_id, variant)
    data = _extras()
    e = data.setdefault(configuration_id, {})
    bugs = OrderedDict(e.get("bgm_bugs") or {})
    if bugs.get(key) == description:
        return False
    if description is None:
        bugs.pop(key, None)
    else:
        bugs[key] = description
    if bugs:
        e["bgm_bugs"] = dict(bugs)
    else:
        e.pop("bgm_bugs", None)
    _save_extras(data)
    return True

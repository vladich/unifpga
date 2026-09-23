#!/usr/bin/env python3
"""
config/profile.py -- design-wiring profiles, config/profiles/<configuration>.yml.

A configuration under config/configurations/ describes hardware: which
peripheral sits on which pins, polarity, widths, clocks, I/O standards, the
constant ties an attached module needs. How the example designs *use* that
hardware is a separate set of conventions: which key resets, which physical
keys and TM1638 bits form the design's buttons / switches / LEDs, that the
design runs on the pixel clock, that a header pin follows the reset. Those
live here, one file per configuration, and config/init.py applies them on
top of the configuration (`synthesize.py --no-profile` or UNIFPGA_PROFILE=0
turns the profile off: buses concatenated in attach order, power-up reset).

    Profile:
      configuration: <id>
      reset:            { sources: [...], sync: 2 }  # codegen's reset vocabulary; sync = flops
                                                      # between the pin releasing and rst
      lab_clock:        pixel | { name: lab, mhz: 50 }
      uart_rx:          0 | 1                         # what the design reads when no UART pin is wired
      tie:              { <ref>: rst | ~rst | 0 | 1 } # pins driven from the reset or tied off
      lab_width:        { buttons: 8 }                # a design bus wider than the bits wired to it
      attach:                                         # per attach, by peripheral and occurrence
        - { peripheral: tm1638_led_key, index: 0,
            lab_bits: {buttons: [0, 1, ...], ...},   # bits of the design bus this attach carries
            params: {as_switches: true, direction: out} }
        - { peripheral: rgb_led, index: 0, drop: true }   # a component left unused
        - { peripheral: seven_segment_per_digit, index: 0,
            bind: {dp: [onboard_leds[4], ...]},       # a signal routed onto other pins
            params: {dp_active: low} }
        - { peripheral: lcd_480_272, index: 0, bind: {hs: null, vs: null} }   # signals tied off
        - { peripheral: lcd_480_272, index: 0, params: {mirror_screen: true} }  # the design gets
                                                      # screen_width - 1 - x, screen_height - 1 - y
"""

import os
from collections import OrderedDict

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE_DIR = os.path.join(REPO, "config", "profiles")

# attach params that belong to the profile, not to the hardware description
PROFILE_PARAMS = ("as_switches", "mirror", "direction", "mirror_screen", "rst", "clock")
REMOVE = object()                                          # set_attach(bind=...): delete an override
TOP_KEYS = ("reset", "lab_clock", "uart_rx", "tie", "lab_width", "attach")


def path_for(configuration_id):
    return os.path.join(PROFILE_DIR, configuration_id + ".yml")


def load(configuration_id):
    """The profile dict (the `Profile:` mapping) or None."""
    p = path_for(configuration_id)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("Profile") or {}


def _header(configuration_id):
    return ("# Design-wiring profile for {c}: how the example designs use the hardware\n"
            "# described in config/configurations/{c}.yml (reset, buses, clock). Applied on\n"
            "# top of it by config/init.py; synthesize.py --no-profile turns it off.\n"
            .format(c=configuration_id))


def _ordered(data):
    """Stable key order for a readable diff."""
    out = OrderedDict()
    for key in ("configuration",) + TOP_KEYS:
        if key in data and data[key] not in (None, {}, []):
            out[key] = data[key]
    for key in data:
        if key not in out and data[key] not in (None, {}, []):
            out[key] = data[key]
    return out


class _Dumper(yaml.SafeDumper):
    pass


def _represent_ordered(dumper, data):
    return dumper.represent_mapping("tag:yaml.org,2002:map", data.items())


_Dumper.add_representer(OrderedDict, _represent_ordered)


def _flow_lists(dumper, data):
    # short lists of ints / None on one line (lab_bits), everything else block style
    if all(x is None or isinstance(x, (int, str)) for x in data) and len(data) <= 40:
        return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=True)
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=False)


_Dumper.add_representer(list, _flow_lists)


def save(configuration_id, data):
    """Write the profile; an empty profile removes the file. Returns True when
    the file changed."""
    data = _ordered(dict(data or {}))
    data.pop("configuration", None)
    p = path_for(configuration_id)
    if not any(k in data for k in TOP_KEYS):
        if os.path.exists(p):
            os.remove(p)
            return True
        return False
    body = OrderedDict([("configuration", configuration_id)])
    body.update(data)
    text = _header(configuration_id) + yaml.dump({"Profile": body}, Dumper=_Dumper, default_flow_style=False,
                                                  sort_keys=False, width=100, allow_unicode=True)
    os.makedirs(PROFILE_DIR, exist_ok=True)
    old = open(p, encoding="utf-8").read() if os.path.exists(p) else None
    if old == text:
        return False
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return True


def update(configuration_id, **fields):
    """Set top-level fields (reset=, lab_clock=, tie=, lab_width=, ...); None removes one."""
    data = load(configuration_id) or {}
    for key, value in fields.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    return save(configuration_id, data)


def attach_override(data, peripheral, index, create=True):
    """The attach override entry for (peripheral, index) in a profile dict."""
    entries = data.setdefault("attach", [])
    for e in entries:
        if e.get("peripheral") == peripheral and int(e.get("index", 0)) == int(index):
            return e
    if not create:
        return None
    e = OrderedDict([("peripheral", peripheral), ("index", int(index))])
    entries.append(e)
    return e


def set_attach(configuration_id, peripheral, index, lab_bits=None, params=None, clear_lab_bits=False,
               drop=None, bind=None):
    """Set an attach's lab_bits, profile params, bind additions and / or drop
    flag in the profile. lab_bits None leaves it, {} / clear_lab_bits removes it;
    params and bind merge (a None value removes a key); drop True / False
    sets / clears the flag."""
    data = load(configuration_id) or {}
    e = attach_override(data, peripheral, index)
    if bind:
        cur = OrderedDict(e.get("bind") or {})
        for k, v in bind.items():
            if v is REMOVE:
                cur.pop(k, None)
            elif v is None:
                cur[k] = None                     # null: unbind the signal
            else:
                cur[k] = list(v) if isinstance(v, (list, tuple)) else v
        if cur:
            e["bind"] = cur
        else:
            e.pop("bind", None)
    if clear_lab_bits or lab_bits == {}:
        e.pop("lab_bits", None)
    elif lab_bits is not None:
        e["lab_bits"] = OrderedDict((k, list(v)) for k, v in lab_bits.items())
    if drop is True:
        e["drop"] = True
    elif drop is False:
        e.pop("drop", None)
    if params:
        cur = OrderedDict(e.get("params") or {})
        for k, v in params.items():
            if v is None:
                cur.pop(k, None)
            else:
                cur[k] = v
        if cur:
            e["params"] = cur
        else:
            e.pop("params", None)
    if len(e) <= 2:                                   # nothing left but the key
        data["attach"] = [x for x in data["attach"] if x is not e]
    return save(configuration_id, data)


def enabled():
    return os.environ.get("UNIFPGA_PROFILE", "1") not in ("0", "false", "no", "off")


# a tool that plans over the configuration's attach list by position keeps
# dropped attaches in place while it resolves, so its indices stay the
# configuration's
KEEP_DROPPED = False


def apply(cfg, attached, profile):
    """Merge a profile into a configuration dict (copied) and the resolved
    attach list (in place). Returns the new configuration dict."""
    if not profile:
        return cfg
    cfg = dict(cfg)
    if profile.get("reset") is not None:
        cfg["reset"] = profile["reset"]
    if profile.get("lab_clock") is not None:
        cfg["lab_clock"] = profile["lab_clock"]
    if profile.get("uart_rx") is not None:
        # what the design's UART receiver reads when no pin is wired (the
        # generic composition gives it the idle line, 1)
        cfg["uart_rx"] = int(profile["uart_rx"])
    if profile.get("tie"):
        tie = OrderedDict(cfg.get("tie") or {})
        tie.update(profile["tie"])
        cfg["tie"] = tie
    if profile.get("lab_width"):
        # the design's bus is wider than the bits wired to it (the extra
        # bits read 0)
        cfg["lab_width"] = {k: int(v) for k, v in profile["lab_width"].items()}
    occ = {}
    dropped = []
    for a in attached:
        pid = a["peripheral_id"]
        i = occ.get(pid, 0)
        occ[pid] = i + 1
        for e in profile.get("attach") or []:
            if e.get("peripheral") == pid and int(e.get("index", 0)) == i:
                if e.get("drop"):
                    # a component the profile leaves unused (its pins come
                    # from `tie`)
                    if not KEEP_DROPPED:
                        dropped.append(a)
                    continue
                if e.get("lab_bits") is not None:
                    a["lab_bits"] = dict(e["lab_bits"])
                if e.get("params"):
                    a["params"] = dict(a.get("params") or {}, **e["params"])
                if e.get("bind"):
                    # a signal routed somewhere the hardware description does
                    # not (a display's decimal point onto LEDs); null unbinds
                    # an optional signal the profile ties off instead
                    b = dict(a.get("bind") or {})
                    for k, v in e["bind"].items():
                        if v is None:
                            b.pop(k, None)
                        else:
                            b[k] = v
                    a["bind"] = b
    for a in dropped:
        attached.remove(a)
    return cfg

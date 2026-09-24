"""
The virtual device a configuration presents to design_top, traced to the
hardware: for every design port (a capability signal: led, btn, sw, abcdefgh,
digit, red, x, sample, io, tx, ...) the attaches that provide it and, bit by
bit where the peripheral wires bits straight to pins, the pinmap entry and the
FPGA pin each design bit reaches. Peripherals with a driver (TM1638, VGA, I2S,
...) reach their pins through that driver: their bits are traced to the
provider and its whole pin set.

The mapping comes from codegen's own capability plans (the same code that
writes top.sv), with the design-wiring profile applied.
"""

import re

from tools import codegen


def _provider_bits(plan, pidx, sig_name):
    """[design bit or None per provider bit] for a per-bit capability signal,
    or None when every provider shares the whole bus."""
    if not codegen._mapped_signal(plan, sig_name):
        return None
    if pidx in plan.bits:
        return list(plan.bits[pidx])
    if pidx in plan.offsets:
        return [plan.offsets[pidx] + k for k in range(plan.widths[pidx])]
    return None


def _direct_signal(perif, cap_id, sig_name):
    """The peripheral signal whose bits are the capability signal's bits one
    to one (led_bank.led, sw_bank.sw, button_array.btn, gpio_header.io,
    rgb_led.r/g/b), or None when a driver sits between them."""
    if perif.get("driver"):
        return None
    names = [s["name"] for s in perif.get("signals") or []]
    if sig_name in names:
        return sig_name
    buses = [s["name"] for s in perif.get("signals") or [] if s.get("type") == "bus"]
    return buses[0] if len(buses) == 1 else None


def pin_links(perif):
    """{pin signal: [{"port": "<capability>.<signal>", "via": <driver module or
    None>, "driver_port": <the driver port the pin meets or None>,
    "port_at": <the driver port the design port meets or None>}]} — which design
    ports each of the peripheral's pins serves, from its contract:

      * pin_assigns `pin.X: capability.C.S`: pin X is design port C.S;
      * driver port_map: a pin on the driver serves every design port on it,
        narrowed by the contract's `serves: {X: [C.S, ...]}` where the pins do
        not all serve all ports (VGA: r serves red, hs serves x);
      * no driver: a pin named like a capability signal it provides (uart tx,
        rx) is that signal.
    """
    out = {}
    names = [s["name"] for s in perif.get("signals") or []]
    provided = {e["capability"] for e in perif.get("provides") or []}
    for lhs, rhs in (perif.get("pin_assigns") or {}).items():
        if str(lhs).startswith("pin.") and str(rhs).startswith("capability."):
            out.setdefault(lhs[4:], []).append({"port": rhs[len("capability."):], "via": None,
                                                "driver_port": None, "port_at": None})
    driver = perif.get("driver") or {}
    if driver:
        pmap = driver.get("port_map") or {}
        caps = []                  # (driver port, capability.signal); a list maps bit by bit
        for port, v in pmap.items():
            for item in (v if isinstance(v, list) else [v]):
                s = str(item)
                if s.startswith("capability."):
                    cap = re.sub(r"\[\d+\]$", "", s[len("capability."):])
                    if (port, cap) not in caps:
                        caps.append((port, cap))
        # a design port copied from one the driver serves (TM1638: btn = sw)
        for lhs, rhs in (perif.get("pin_assigns") or {}).items():
            if str(lhs).startswith("capability.") and str(rhs).startswith("capability."):
                src = str(rhs)[len("capability."):]
                caps += [(port, str(lhs)[len("capability."):]) for port, cap in list(caps) if cap == src]
        serves = perif.get("serves") or {}
        for port, v in pmap.items():
            if not str(v).startswith("pin."):
                continue
            sig = v[4:]
            wanted = serves.get(sig)
            for cport, cap in caps:
                if wanted is None or cap in wanted:
                    out.setdefault(sig, []).append({"port": cap, "via": driver.get("module"),
                                                    "driver_port": port, "port_at": cport})
    else:
        for sig in names:
            for cap in provided:
                if any(s["name"] == sig for s in (config_capabilities().get(cap) or {}).get("signals") or []):
                    out.setdefault(sig, []).append({"port": cap + "." + sig, "via": None,
                                                    "driver_port": None, "port_at": None})
    return out


_CAPS = {}


def config_capabilities():
    if not _CAPS:
        from config import init as config_init
        _CAPS.update(config_init.read_capabilities())
    return _CAPS


def _pins(pinmap, ref):
    return [{"ref": bit, "pin": pin} for bit, pin in codegen._bind_pins(pinmap, ref)]


def trace(resolved):
    """{"ports": [...], "attaches": [...]} — see the module docstring.

    ports: {capability, signal, direction, providers: [{attach_index, peripheral,
            bits: [{design_bit, provider_bit, ref, pin}] | None, via, pins}]}
    attaches: per resolved attach, {attach_index, peripheral, pins: {signal: [{ref, pin}]},
               links: {signal: pin_links() entries}}.
    """
    plans = codegen.build_capability_plans(resolved)
    pinmap = resolved["board_pinmap"]
    attaches = []
    for a in resolved["peripherals"]:
        links = pin_links(a["peripheral"])
        attaches.append({"attach_index": a.get("attach_index"), "peripheral": a["peripheral_id"],
                         "pins": {sig: _pins(pinmap, ref) for sig, ref in (a.get("bind") or {}).items()},
                         "links": {sig: links.get(sig, []) for sig in (a.get("bind") or {})}})
    ports = []
    for cap_id, plan in plans.items():
        if not plan.providers:
            continue
        for sig in plan.cap.get("signals") or []:
            port = {"capability": cap_id, "signal": sig["name"], "direction": sig.get("direction"),
                    "providers": []}
            for pidx, perif, _params in plan.providers:
                a = resolved["peripherals"][pidx]
                entry = {"attach_index": a.get("attach_index"), "peripheral": a["peripheral_id"],
                         "via": (perif.get("driver") or {}).get("module"), "bits": None,
                         "pins": attaches[pidx]["pins"]}
                design_bits = _provider_bits(plan, pidx, sig["name"])
                direct = _direct_signal(perif, cap_id, sig["name"])
                if design_bits is not None and direct and direct in (a.get("bind") or {}):
                    pins = _pins(pinmap, a["bind"][direct])
                    if codegen._peripheral_mirror(a, pinmap):
                        pins = list(reversed(pins))
                    entry["bits"] = [{"design_bit": b, "provider_bit": k,
                                      "ref": pins[k]["ref"] if k < len(pins) else None,
                                      "pin": pins[k]["pin"] if k < len(pins) else None}
                                     for k, b in enumerate(design_bits)]
                elif design_bits is not None:
                    entry["bits"] = [{"design_bit": b, "provider_bit": k, "ref": None, "pin": None}
                                     for k, b in enumerate(design_bits)]
                port["providers"].append(entry)
            ports.append(port)
    return {"ports": ports, "attaches": attaches}

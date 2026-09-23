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


def _pins(pinmap, ref):
    return [{"ref": bit, "pin": pin} for bit, pin in codegen._bind_pins(pinmap, ref)]


def trace(resolved):
    """{"ports": [...], "attaches": [...]} — see the module docstring.

    ports: {capability, signal, direction, providers: [{attach_index, peripheral,
            bits: [{design_bit, provider_bit, ref, pin}] | None, via, pins}]}
    attaches: per resolved attach, {attach_index, peripheral, pins: {signal: [{ref, pin}]}}.
    """
    plans = codegen.build_capability_plans(resolved)
    pinmap = resolved["board_pinmap"]
    attaches = []
    for a in resolved["peripherals"]:
        attaches.append({"attach_index": a.get("attach_index"), "peripheral": a["peripheral_id"],
                         "pins": {sig: _pins(pinmap, ref) for sig, ref in (a.get("bind") or {}).items()}})
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

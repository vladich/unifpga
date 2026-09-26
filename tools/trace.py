"""
The virtual device a configuration presents to design_top, traced to the
hardware: for every design port (a capability signal: led, btn, sw, abcdefgh,
digit, red, x, sample, io, tx, ...) the attaches that provide it and, bit by
bit where the peripheral wires bits straight to pins, the board entry and the
FPGA pin each design bit reaches. Peripherals with a driver (TM1638, VGA, I2S,
...) reach their pins through that driver: their bits are traced to the
provider and its whole pin set.

The mapping comes from codegen's own capability plans (the same code that
writes top.sv), the rig's design section applied.
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
        not all serve all ports (VGA: r serves red, hs serves x), or
        `serves: {X: [[...], [...]]}` pin by pin of a bus (HDMI: d_p[0] carries
        blue and the syncs, d_p[1] green, d_p[2] red; the link then has `pins`);
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
            # `serves: {d_p: [[blue, x, y], [green], [red]]}`: one list per pin of a bus
            per_pin = isinstance(wanted, list) and bool(wanted) and all(isinstance(w, list) for w in wanted)
            for cport, cap in caps:
                link = {"port": cap, "via": driver.get("module"), "driver_port": port, "port_at": cport}
                if per_pin:
                    link["pins"] = [k for k, w in enumerate(wanted) if cap in w]
                    if not link["pins"]:
                        continue
                elif wanted is not None and cap not in wanted:
                    continue
                out.setdefault(sig, []).append(link)
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


def _pins(board, ref):
    return [{"ref": bit, "pin": pin} for bit, pin in codegen._bind_pins(board, ref)]


def pin_sources(resolved, idx, clocks):
    """{pin signal: {"kind", "text"}} — the pins of attach `idx` the FPGA drives
    from something other than a design bit: its clock tree (`pin.ck:
    clock.pixel`), a level (`pin.reset: const.0`), the design's reset or the
    board clock (`context.rst`, `context.clk`), or a parameter whose value is
    one of those (`pin.bl: $bl`). `clocks` is collect_clock_requirements()."""
    a = resolved["peripherals"][idx]
    perif, params = a["peripheral"], a.get("params") or {}
    out = {}
    for lhs, rhs in (perif.get("pin_assigns") or {}).items():
        if not str(lhs).startswith("pin."):
            continue
        sig = re.sub(r"\[\d+\]$", "", lhs[4:])
        if sig not in (a.get("bind") or {}):
            continue
        value = rhs
        if isinstance(value, str) and value.startswith("$"):
            value = codegen._eval_param(value, params, perif)
            if value is None:
                continue
        inverted = isinstance(value, str) and value.strip().startswith("~")
        text = str(value).strip().lstrip("~ ").strip() if isinstance(value, str) else value
        if isinstance(text, str) and text.startswith("capability."):
            continue                                   # a design bit: an edge, not a source
        if isinstance(text, str) and text.startswith("clock."):
            name = text[len("clock."):].split(".")[0]
            req = clocks.get(name) or {}
            mhz = req.get("mhz")
            how = "{:g} MHz from the PLL".format(mhz) if mhz else ("{} / {}".format(req["from"], req["divide"]) if req.get("from") else "from the PLL")
            src = {"kind": "clock", "text": "the {} clock, {}".format(name, how)}
        elif isinstance(text, str) and text.startswith("const."):
            src = {"kind": "tied", "text": "tied to {}".format(text[len("const."):])}
        elif isinstance(text, str) and text.startswith("context."):
            name = text[len("context."):]
            src = {"kind": "reset" if name.startswith("rst") else "clock",
                   "text": {"rst": "the design's reset", "rst_n": "the design's reset (active low)", "clk": "the board clock"}.get(name, name)}
        elif isinstance(text, (bool, int)) and not isinstance(text, str):
            src = {"kind": "tied", "text": "tied to {}".format(int(text))}
        else:
            continue
        if inverted:
            src["text"] += ", inverted"
        out[sig] = src
    return out


def trace(resolved):
    """{"ports": [...], "attaches": [...]} — see the module docstring.

    ports: every design_top port in its order: {design_port, width, capability, signal,
            direction, providers: [{attach_index, peripheral,
            bits: [{design_bit, provider_bit, ref, pin}] | None, via, pins}]}
    attaches: per resolved attach, {attach_index, peripheral, pins: {signal: [{ref, pin}]},
               links: {signal: pin_links() entries}, sources: pin_sources() (pins the
               FPGA drives from its clock tree, a level or the reset, not a design bit)}.
    edges: design bit <-> pin, see edges().
    """
    plans = codegen.build_capability_plans(resolved)
    board = resolved["board"]
    attaches = []
    clocks = codegen.collect_clock_requirements(resolved)
    for idx, a in enumerate(resolved["peripherals"]):
        links = pin_links(a["peripheral"])
        attaches.append({"attach_index": a.get("attach_index"), "peripheral": a["peripheral_id"],
                         "params": a.get("params") or {},
                         "pin_fit": a["peripheral"].get("pin_fit") or {},
                         "pins": {sig: _pins(board, ref) for sig, ref in (a.get("bind") or {}).items()},
                         "links": {sig: links.get(sig, []) for sig in (a.get("bind") or {})},
                         "sources": pin_sources(resolved, idx, clocks)})
    # a gpio bit whose pin another part uses dangles (codegen): no edge to the pin
    gpio_plan = plans.get("gpio")
    gpio_indices = {pidx for pidx, _p, _q in gpio_plan.providers} if gpio_plan else set()
    claimed = codegen._claimed_port_bits(resolved, plans, gpio_indices) if gpio_indices else set()
    parameters = codegen.design_top_parameters(resolved, plans)
    widths = codegen.design_top_widths(parameters)
    ports = []
    for contract_port in codegen.design_contract()[2]:
        design_port, cap_id, sig_name, width = contract_port[:4]
        plan = plans.get(cap_id)
        if contract_port.optional and not (plan and plan.providers):
            continue                      # an optional capability this rig lacks: not on the device
        sig = next((s for s in (plan.cap.get("signals") or []) if s["name"] == sig_name), {}) if plan else {}
        port = {"design_port": design_port,
                "width": codegen.design_port_width(width, widths) if plan and plan.providers else 0,
                "width_parameter": width if isinstance(width, str) else None,
                "capability": cap_id, "signal": sig_name, "direction": sig.get("direction"), "providers": []}
        for pidx, perif, _params in (plan.providers if plan else []):
            a = resolved["peripherals"][pidx]
            entry = {"attach_index": a.get("attach_index"), "peripheral": a["peripheral_id"],
                     "via": (perif.get("driver") or {}).get("module"), "bits": None,
                     "pins": attaches[pidx]["pins"]}
            design_bits = _provider_bits(plan, pidx, sig_name)
            direct = _direct_signal(perif, cap_id, sig_name)
            if design_bits is not None and direct and direct in (a.get("bind") or {}):
                pins = _pins(board, a["bind"][direct])
                if codegen._peripheral_mirror(a, board):
                    pins = list(reversed(pins))
                taken = lambda k: pidx in gpio_indices and k < len(pins) and pins[k]["ref"] and \
                    set(codegen._bind_bit_ports(resolved, pins[k]["ref"])) & claimed
                entry["bits"] = [{"design_bit": b, "provider_bit": k,
                                  "ref": pins[k]["ref"] if k < len(pins) and not taken(k) else None,
                                  "pin": pins[k]["pin"] if k < len(pins) and not taken(k) else None}
                                 for k, b in enumerate(design_bits)]
            elif design_bits is not None:
                entry["bits"] = [{"design_bit": b, "provider_bit": k, "ref": None, "pin": None}
                                 for k, b in enumerate(design_bits)]
            port["providers"].append(entry)
        ports.append(port)
    gpio_uses = {resolved["peripherals"][pidx].get("attach_index") for pidx in gpio_indices}
    kept = [e for e in edges(ports, attaches)
            if not (e["use"] in gpio_uses and set(codegen._bind_bit_ports(resolved, e["ref"])) & claimed)]
    return {"ports": ports, "attaches": attaches, "edges": kept, "parameters": dict(widths)}


def _fit(bits, npins, k, fit):
    """(design bit or None, relation) for pin k of a driver signal with npins
    pins serving the design bits `bits`:
      bit     as many pins as bits (or one of each): pin k is bits[k]
      msb     fewer pins, the driver keeps the top bits (pin_fit: msb)
      or      one pin, the driver ORs the bits (pin_fit: msb)
      shared  the pin carries all the bits (serial, multiplexed or timing)"""
    if len(bits) == npins:
        return bits[k], "bit"
    if len(bits) == 1:
        return bits[0], "bit"
    if fit == "msb" and npins == 1:
        return None, "or"
    if fit == "msb" and npins < len(bits):
        return bits[len(bits) - npins + k], "msb"
    return None, "shared"


def edges(ports, attaches):
    """Design bit <-> pin edges: [{design_port, bit, bits, use, signal, ref,
    pin, via, relation}]. Direct providers give one edge per bit (relation
    direct). A driver's pins give one per pin, as _fit() relates them: bit for
    bit where the pin bus is as wide as the design port (VGA r[k] <-> red[k]),
    the top bits or an OR where the peripheral's pin_fit says the driver
    narrows the port so, else `bit` None and `bits` the design bits the
    provider occupies in that port (a TM1638 given led[0..7] by lab_bits; all
    bits of a port without a per-bit mapping, such as mic_sample or x). A port
    the provider occupies no bit of gets no edge."""
    out = []
    by_key = {p["capability"] + "." + p["signal"]: p for p in ports}

    def occupied(port, use):
        """The design bits `use` provides in `port`: a list, or None for
        all of them (no per-bit mapping)."""
        for pr in port["providers"]:
            if pr["attach_index"] == use:
                if pr["bits"] is None:
                    return None
                return sorted({b["design_bit"] for b in pr["bits"] if b["design_bit"] is not None})
        return []
    for p in ports:
        for pr in p["providers"]:
            for b in pr["bits"] or []:
                if b["ref"] and b["design_bit"] is not None:
                    out.append({"design_port": p["design_port"], "bit": b["design_bit"], "bits": [b["design_bit"]],
                                "use": pr["attach_index"], "signal": None, "ref": b["ref"], "pin": b["pin"], "via": None,
                                "relation": "direct"})
    direct = {(e["use"], e["ref"]) for e in out}
    for a in attaches:
        for sig, links in (a.get("links") or {}).items():
            pins = a["pins"].get(sig) or []
            for link in links:
                port = by_key.get(link["port"])
                if port is None or not port["providers"]:
                    continue
                bits = occupied(port, a["attach_index"])
                if bits == []:
                    continue                  # the provider has no bit of this port
                if bits is None:
                    bits = list(range(port["width"]))
                fit = (a.get("pin_fit") or {}).get(sig)
                for k, pin in enumerate(pins):
                    if (a["attach_index"], pin["ref"]) in direct or ("pins" in link and k not in link["pins"]):
                        continue
                    bit, relation = _fit(bits, len(pins), k, fit)
                    out.append({"design_port": port["design_port"], "bit": bit,
                                "bits": [bit] if bit is not None else bits, "use": a["attach_index"],
                                "signal": sig, "ref": pin["ref"], "pin": pin["pin"], "via": link["via"],
                                # the driver's own ports: the one the pin meets, the one the design port meets
                                "driver_port": link.get("driver_port"), "driver_design_port": link.get("port_at"),
                                "relation": relation})
    return out

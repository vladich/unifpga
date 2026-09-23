"""
Schematic-style drawings of a board layout or a setup (tools/setup.py), as
standalone HTML with an inline SVG, and a local web page that serves them.

The board is a box: its connectors drawn as pin grids down the right edge (hover
a pin for its pinmap entry and FPGA pin), its on-board devices listed inside
(the ones a setup uses highlighted). A setup adds its modules to the right of
the board with a coloured wire from each module pin to the connector pin it is
on, marks connectors handed to the design as gpio, and lists what validate()
reports above the drawing.
"""

import html
import os

from config import init as config_init
from tools import codegen
from tools import setup as su

PIN = 14                   # pin pitch
BOARD_X, BOARD_Y = 20, 60
PALETTE = ("#d9480f", "#1971c2", "#2f9e44", "#9c36b5", "#e67700", "#0c8599", "#c2255c", "#5c940d")


def _esc(s):
    return html.escape(str(s), quote=True)


def _fpga_pin(pinmap, ref):
    pins = [p for _b, p in codegen._bind_pins(pinmap, ref) if p]
    return ", ".join(str(p) for p in pins) or "?"


def _connector_grid(conn, ctype):
    """[[pin key, ...], ...] rows to draw for a connector."""
    rows = (ctype or {}).get("rows")
    if rows:
        return [[str(k) for k in r] for r in rows]
    keys = [str(k) for k in (conn.get("pins") or {})]
    half = (len(keys) + 1) // 2
    return [keys[:half], keys[half:]]


def render_svg(board_id, setup=None):
    layout = su.read_layout(board_id)
    connectors = su.read_connectors()
    pinmap = config_init.read_board_pinmap(board_id) or {}
    modules = su.read_modules()

    used_onboard, gpio_conns = set(), set()
    module_boxes = []
    if setup:
        for use in setup.get("use") or []:
            if "onboard" in use:
                used_onboard.add(use["onboard"])
            elif "gpio" in use:
                gpio_conns.add(use["gpio"])
            elif "module" in use:
                colour = PALETTE[len(module_boxes) % len(PALETTE)]
                module = modules.get(use["module"]) or {"pins": {}, "name": use["module"]}
                w = use.get("wires") or {}
                if "plug" in use:
                    w = su.plug_wires(connectors, layout, module, use["plug"])
                w = {str(k): v for k, v in w.items()}
                module_boxes.append((use, module, colour, w))

    # ---- connectors: grids down the right edge of the board
    grid_x = BOARD_X + 300
    y = BOARD_Y + 40
    pin_xy, parts = {}, []
    widest = 0
    for conn in layout.get("connectors") or []:
        ctype = connectors.get(conn["type"]) or {}
        grid = _connector_grid(conn, ctype)
        width = max(len(r) for r in grid) * PIN
        widest = max(widest, width)
        power = {str(k): v for k, v in (ctype.get("power") or {}).items()}
        refs = {str(k): v for k, v in (conn.get("pins") or {}).items()}
        outline = "#2b8a3e" if conn["id"] in gpio_conns else "#495057"
        parts.append('<rect x="{}" y="{}" width="{}" height="{}" rx="3" fill="#f8f9fa" stroke="{}" stroke-width="{}"/>'
                     .format(grid_x - 6, y - 6, width + 12, len(grid) * PIN + 12, outline,
                             2.5 if conn["id"] in gpio_conns else 1))
        label = conn.get("label") or conn["id"]
        if conn["id"] in gpio_conns:
            label += "  (design gpio)"
        parts.append('<text x="{}" y="{}" class="clabel">{}</text>'.format(grid_x - 6, y - 10, _esc(label)))
        for r, row in enumerate(grid):
            for c, key in enumerate(row):
                cx, cy = grid_x + c * PIN + PIN / 2, y + r * PIN + PIN / 2
                pin_xy[(conn["id"], key)] = (cx, cy)
                ref = refs.get(key)
                if ref is not None:
                    tip = "{}.{}: {} = {}".format(conn["id"], key, ref, _fpga_pin(pinmap, ref))
                    fill = "#ffffff"
                elif key in power:
                    tip = "{}.{}: {}".format(conn["id"], key, power[key])
                    fill = "#ffc9c9" if power[key] == "VCC" else "#ced4da"
                else:
                    tip, fill = "{}.{}".format(conn["id"], key), "#e9ecef"
                parts.append('<circle cx="{:.1f}" cy="{:.1f}" r="5" fill="{}" stroke="#495057"><title>{}</title></circle>'
                             .format(cx, cy, fill, _esc(tip)))
        y += len(grid) * PIN + 34
    board_h = max(y - BOARD_Y, 60 + 22 * len(layout.get("onboard") or []))

    # ---- on-board devices, inside the board on the left
    oy = BOARD_Y + 40
    for o in layout.get("onboard") or []:
        used = o["id"] in used_onboard
        a = o["attach"]
        tip = "{} -> {} {}".format(o["id"], a["peripheral"], a.get("bind"))
        parts.append('<g><title>{}</title><rect x="{}" y="{}" width="260" height="18" rx="3" fill="{}" stroke="#868e96"/>'
                     '<text x="{}" y="{}" class="dev">{}</text></g>'
                     .format(_esc(tip), BOARD_X + 12, oy, "#d3f9d8" if used else "#ffffff",
                             BOARD_X + 18, oy + 13, _esc(o.get("label") or o["id"])))
        oy += 22
    board_w = grid_x - BOARD_X + widest + 24
    frame = ('<rect x="{}" y="{}" width="{}" height="{}" rx="8" fill="#e7f5ff" stroke="#1c7ed6" stroke-width="2"/>'
             '<text x="{}" y="{}" class="title">{}</text>'
             .format(BOARD_X, BOARD_Y, board_w, board_h, BOARD_X + 12, BOARD_Y + 24, _esc(layout["board"])))

    # ---- modules to the right, wires to their connector pins
    mx, my = BOARD_X + board_w + 180, BOARD_Y
    for use, module, colour, w in module_boxes:
        pins = [p for p, sig in module["pins"].items() if sig not in su._PASSIVE and p in w]
        h = 26 + PIN * max(len(pins), 1)
        parts.append('<rect x="{}" y="{}" width="210" height="{}" rx="5" fill="#fff" stroke="{}" stroke-width="2"/>'
                     '<text x="{}" y="{}" class="mod" fill="{}">{}</text>'
                     .format(mx, my, h, colour, mx + 8, my + 16, colour, _esc(module.get("name") or module.get("id"))))
        for i, p in enumerate(pins):
            py = my + 26 + i * PIN + PIN / 2
            where = w[p]
            conn_id, _, key = where.partition(".")
            parts.append('<text x="{}" y="{:.1f}" class="pin">{} ({})</text>'
                         .format(mx + 8, py + 4, _esc(p), _esc(module["pins"].get(p))))
            if (conn_id, key) in pin_xy:
                cx, cy = pin_xy[(conn_id, key)]
                parts.append('<path d="M{:.1f},{:.1f} C{:.1f},{:.1f} {:.1f},{:.1f} {:.1f},{:.1f}" fill="none" '
                             'stroke="{}" stroke-width="1.6" opacity="0.85"><title>{} {} -> {}</title></path>'
                             .format(mx, py, mx - 90, py, cx + 60, cy, cx + 5, cy, colour,
                                     _esc(module.get("id")), _esc(p), _esc(where)))
        if "plug" in use:
            parts.append('<text x="{}" y="{}" class="pin">plugged into {} row {}</text>'
                         .format(mx, my + h + 12, _esc(use["plug"]["connector"]), use["plug"]["row"]))
            h += 12
        my += h + 20

    width = max(mx + 240, BOARD_X + board_w + 40)
    height = max(BOARD_Y + board_h, my) + 30
    style = ("<style>.title{font:bold 16px sans-serif}.clabel{font:11px sans-serif;fill:#343a40}"
             ".dev{font:11px sans-serif}.mod{font:bold 12px sans-serif}.pin{font:10px monospace;fill:#343a40}</style>")
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="{}" height="{}" viewBox="0 0 {} {}">{}{}{}</svg>'
            .format(width, height, width, height, style, frame, "".join(parts)))


def render_page(board_id=None, setup_id=None):
    """A standalone HTML page for a board layout or a setup."""
    setup = su.read_setup(setup_id) if setup_id else None
    board_id = setup["board"] if setup else board_id
    title = setup_id or board_id
    notes = ""
    if setup:
        problems = su.validate(setup)
        items = "".join('<li class="{}">{}</li>'.format(level, _esc(msg)) for level, msg in problems)
        notes = ('<p>{} on {} with {}.</p>'.format(_esc(setup_id), _esc(board_id), _esc(setup["toolchain"])) +
                 ('<ul>{}</ul>'.format(items) if items else '<p class="ok">No problems found.</p>'))
    layout = su.read_layout(board_id)
    src = "" if layout.get("verified") else '<p class="warning">Layout not verified against vendor sources.</p>'
    return ("<!doctype html><html><head><meta charset='utf-8'><title>{t}</title><style>"
            "body{{font:14px sans-serif;margin:16px;background:#fff;color:#212529}}"
            ".error{{color:#c92a2a}}.warning{{color:#e67700}}.ok{{color:#2b8a3e}}a{{color:#1c7ed6}}"
            "</style></head><body><p><a href='/'>all boards and setups</a></p><h2>{t}</h2>{src}{notes}{svg}"
            "</body></html>").format(t=_esc(title), src=src, notes=notes, svg=render_svg(board_id, setup))


def render_index():
    layouts = su.read_layouts()
    setups = su.read_setups()
    rows = []
    for board_id in sorted(layouts):
        own = sorted(s for s, v in setups.items() if v["board"] == board_id)
        rows.append("<li><a href='/board/{b}'>{b}</a><ul>{s}</ul></li>".format(
            b=_esc(board_id), s="".join("<li><a href='/setup/{0}'>{0}</a></li>".format(_esc(s)) for s in own)))
    return ("<!doctype html><html><head><meta charset='utf-8'><title>unifpga boards</title>"
            "<style>body{{font:14px sans-serif;margin:16px}}a{{color:#1c7ed6}}</style></head><body>"
            "<h2>Boards with a layout, and their setups</h2><ul>{}</ul></body></html>").format("".join(rows))


def make_server(port=8765, host="127.0.0.1"):
    """An HTTP server for the index (/), /board/<id> and /setup/<id>."""
    import http.server

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parts = [p for p in self.path.split("?")[0].split("/") if p]
            try:
                if not parts:
                    body = render_index()
                elif len(parts) == 2 and parts[0] == "board":
                    body = render_page(board_id=parts[1])
                elif len(parts) == 2 and parts[0] == "setup":
                    body = render_page(setup_id=parts[1])
                else:
                    self.send_error(404)
                    return
            except (su.SetupError, config_init.ConfigError) as exc:
                self.send_error(404, str(exc))
                return
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt, *args):
            pass

    return http.server.ThreadingHTTPServer((host, port), Handler)


def serve(port=8765, host="127.0.0.1"):
    """Serve the drawings on http://host:port/ until interrupted."""
    httpd = make_server(port, host)
    print("Serving board drawings on http://{}:{}/ (Ctrl-C stops)".format(host, port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def write_page(path, board_id=None, setup_id=None):
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_page(board_id=board_id, setup_id=setup_id))
    return os.path.abspath(path)

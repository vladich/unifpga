"""
The board editor: a local web page (./unifpga serve) that draws a setup —
the virtual device design_top sees, the board with its on-board devices and
connectors, the add-on modules and their wires — and edits it: add, rewire and
remove modules, use on-board devices or not, hand connectors to the design as
gpio, set parameters. Every edit is evaluated by the same code the build uses
(tools/setup.py generate + validate, config/init.py resolve, tools/trace.py),
so what the page shows is what synthesis will get. Saving writes
config/setups/<id>.yml and the configuration it generates; "project" writes
the SystemVerilog project for a design (the dry run of synthesize.py into
designs/<design>/run/<id>/).

The page itself is tools/studio/ (index.html, studio.js, studio.css); the
server only answers JSON. It listens on 127.0.0.1 and refuses a write that
does not carry the page's own header and a local Origin, so another web site
open in the same browser cannot drive it.
"""

import io
import json
import os
import re
import zipfile

from config import init as config_init
from tools import codegen
from tools import setup as su
from tools import trace as tr

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(REPO, "tools", "studio")
DESIGNS_DIR = os.path.join(REPO, "designs")
ID_RE = re.compile(r"^[a-z0-9_]{1,80}$")
WRITE_HEADER = "X-Unifpga-Studio"


class ApiError(Exception):
    def __init__(self, status, message):
        Exception.__init__(self, message)
        self.status = status


# ---------------------------------------------------------------------------
# data for the page
# ---------------------------------------------------------------------------

def list_designs():
    if not os.path.isdir(DESIGNS_DIR):
        return []
    return sorted(d for d in os.listdir(DESIGNS_DIR)
                  if os.path.isfile(os.path.join(DESIGNS_DIR, d, "design_top.sv")))


def board_data(board_id):
    """Everything the page needs to draw and edit rigs on one board."""
    layouts = su.read_layouts()
    if board_id not in layouts:
        raise ApiError(404, "no layout for board '{}'".format(board_id))
    layout = layouts[board_id]
    pinmap = config_init.read_board_pinmap(board_id) or {}
    ctypes = su.read_connectors()

    def pins(ref):
        return [{"ref": bit, "pin": pin} for bit, pin in codegen._bind_pins(pinmap, ref)]

    connectors = []
    for c in layout.get("connectors") or []:
        ctype = ctypes.get(c["type"]) or {}
        rows = [[str(k) for k in r] for r in ctype.get("rows") or []]
        keys = [str(k) for k in c.get("pins") or {}]
        if not rows:
            half = (len(keys) + 1) // 2
            rows = [keys[:half], keys[half:]]
        connectors.append({
            "id": c["id"], "label": c.get("label") or c["id"], "type": c["type"], "bank": c.get("bank"),
            "voltage": ctype.get("voltage"), "rows": rows,
            "power": {str(k): v for k, v in (ctype.get("power") or {}).items()},
            "pins": {str(k): {"ref": ref, "pin": ", ".join(p["pin"] or "?" for p in pins(ref))}
                     for k, ref in (c.get("pins") or {}).items()},
        })
    onboard = [{"id": o["id"], "label": o.get("label") or o["id"], "attach": o["attach"],
                "pins": {s: pins(ref) for s, ref in (o["attach"].get("bind") or {}).items()}}
               for o in layout.get("onboard") or []]
    peripherals = config_init.read_peripherals()
    modules = su.read_modules()
    used = {m["peripheral"] for m in modules.values()} | {"gpio_header"} | \
           {o["attach"]["peripheral"] for o in layout.get("onboard") or []}
    return {
        "board": board_id,
        "verified": bool(layout.get("verified")),
        "connectors": connectors,
        "onboard": onboard,
        "modules": sorted(modules.values(), key=lambda m: m["id"]),
        "peripherals": {pid: {"description": p.get("description"), "signals": p.get("signals") or [],
                              "parameters": p.get("parameters") or {}, "provides": p.get("provides") or [],
                              "driver": (p.get("driver") or {}).get("module") if p.get("driver") else None}
                        for pid, p in peripherals.items() if pid in used},
        "setups": sorted(s for s, v in su.read_setups().items() if v["board"] == board_id),
        "toolchains": sorted(config_init.read_toolchains()),
        "designs": list_designs(),
    }


def evaluate(setup):
    """What the build makes of a (possibly unsaved) setup: problems, the
    configuration text, and the virtual device traced to pins."""
    out = {"problems": [], "configuration_text": None, "trace": None, "profile": None}
    from config import profile
    if profile.enabled() and profile.load(setup.get("id")):
        out["profile"] = os.path.relpath(profile.path_for(setup["id"]), REPO)
    try:
        cfg = su.generate(setup)
    except su.SetupError as exc:
        out["problems"] = [{"level": "error", "message": str(exc)}]
        return out
    out["problems"] = [{"level": level, "message": msg} for level, msg in su.validate(setup)]
    out["configuration_text"] = su.emit_configuration(cfg, setup.get("notes"))
    if any(p["level"] == "error" for p in out["problems"]):
        return out
    try:
        resolved = config_init.resolve_configuration(setup["id"], configuration=cfg)
        out["trace"] = tr.trace(resolved)
    except (config_init.ConfigError, codegen.CodegenError) as exc:
        out["problems"].append({"level": "error", "message": str(exc)})
    return out


def _check_setup(setup):
    if not isinstance(setup, dict) or not ID_RE.match(str(setup.get("id", ""))):
        raise ApiError(400, "a setup id is 1-80 characters of a-z, 0-9 and _")
    if setup.get("board") not in su.read_layouts():
        raise ApiError(400, "unknown board '{}'".format(setup.get("board")))
    # an existing configuration without a setup belongs to the hand-written /
    # synced data: do not overwrite it from here
    if setup["id"] in config_init.read_configurations() and setup["id"] not in su.read_setups():
        raise ApiError(409, "configuration '{}' exists and has no setup; choose another id".format(setup["id"]))


def save(setup):
    _check_setup(setup)
    result = evaluate(setup)
    errors = [p["message"] for p in result["problems"] if p["level"] == "error"]
    if errors:
        raise ApiError(400, "not saved: " + "; ".join(errors))
    setup_path = su.write_setup(setup)
    cfg_path = su.configuration_path(setup["id"])
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write(result["configuration_text"])
    config_init.clear_cache()
    return {"setup": os.path.relpath(setup_path, REPO), "configuration": os.path.relpath(cfg_path, REPO)}


def project(setup_id, design):
    """The SystemVerilog project for a saved setup and a design:
    designs/<design>/run/<setup_id>/ (top.sv, constraints, toolchain project)."""
    if not ID_RE.match(str(setup_id)) or setup_id not in su.read_setups():
        raise ApiError(400, "save the setup first")
    if design not in list_designs():
        raise ApiError(400, "unknown design '{}'".format(design))
    from tools import cli
    design_dir = os.path.join(DESIGNS_DIR, design)
    rc = cli.prepare_design(design_dir, setup_id)
    out = cli.run_dir(design_dir, setup_id)
    files = sorted(os.path.relpath(os.path.join(root, f), out)
                   for root, _dirs, names in os.walk(out) for f in names)
    return {"ok": rc == 0, "output": os.path.relpath(out, REPO), "files": files}


def project_zip(setup_id, design):
    from tools import cli
    if not ID_RE.match(str(setup_id)) or design not in list_designs():
        raise ApiError(404, "no such project")
    out = cli.run_dir(os.path.join(DESIGNS_DIR, design), setup_id)
    if not os.path.isdir(out):
        raise ApiError(404, "generate the project first")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(os.path.join(DESIGNS_DIR, design, "design_top.sv"), "design_top.sv")
        for root, _dirs, names in os.walk(out):
            for f in names:
                full = os.path.join(root, f)
                z.write(full, os.path.join(setup_id, os.path.relpath(full, out)))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

_STATIC_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8"}


def make_server(port=8765, host="127.0.0.1"):
    import http.server

    class Handler(http.server.BaseHTTPRequestHandler):
        def _send(self, status, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = self.path.split("?")[0]
            parts = [p for p in path.split("/") if p]
            try:
                if not parts or parts == ["index.html"]:
                    return self._static("index.html")
                if parts[0] in ("studio.js", "studio.css") and len(parts) == 1:
                    return self._static(parts[0])
                if parts[:2] == ["api", "boards"]:
                    return self._send(200, sorted(su.read_layouts()))
                if parts[:2] == ["api", "board"] and len(parts) == 3:
                    return self._send(200, board_data(parts[2]))
                if parts[:2] == ["api", "setup"] and len(parts) == 3:
                    setups = su.read_setups()
                    if parts[2] not in setups:
                        raise ApiError(404, "no setup '{}'".format(parts[2]))
                    return self._send(200, setups[parts[2]])
                if parts[:2] == ["api", "project"] and len(parts) == 4 and parts[3].endswith(".zip"):
                    data = project_zip(parts[2], parts[3][:-4])
                    return self._send(200, data, "application/zip")
                raise ApiError(404, "not found")
            except ApiError as exc:
                return self._send(exc.status, {"error": str(exc)})
            except (su.SetupError, config_init.ConfigError, codegen.CodegenError) as exc:
                return self._send(400, {"error": str(exc)})

        def do_POST(self):
            try:
                self._check_origin()
                length = int(self.headers.get("Content-Length") or 0)
                if length > 1 << 20:
                    raise ApiError(413, "request too large")
                body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                path = self.path.split("?")[0]
                if path == "/api/evaluate":
                    return self._send(200, evaluate(body["setup"]))
                if path == "/api/save":
                    return self._send(200, save(body["setup"]))
                if path == "/api/project":
                    return self._send(200, project(body.get("setup_id"), body.get("design")))
                raise ApiError(404, "not found")
            except ApiError as exc:
                return self._send(exc.status, {"error": str(exc)})
            except (KeyError, ValueError, TypeError) as exc:
                return self._send(400, {"error": "bad request: {}".format(exc)})
            except (su.SetupError, config_init.ConfigError, codegen.CodegenError) as exc:
                return self._send(400, {"error": str(exc)})

        def _check_origin(self):
            # a cross-site page can POST to localhost, but not with a custom
            # header (the preflight fails) and not with our Origin
            if self.headers.get(WRITE_HEADER) != "1":
                raise ApiError(403, "missing " + WRITE_HEADER)
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
            if host not in ("127.0.0.1", "localhost"):
                raise ApiError(403, "foreign host")        # DNS rebinding
            origin = self.headers.get("Origin")
            if origin and not re.match(r"^http://(127\.0\.0\.1|localhost)(:\d+)?$", origin):
                raise ApiError(403, "foreign origin")

        def _static(self, name):
            with open(os.path.join(STATIC, name), "rb") as f:
                return self._send(200, f.read(), _STATIC_TYPES[os.path.splitext(name)[1]])

        def log_message(self, fmt, *args):
            pass

    return http.server.ThreadingHTTPServer((host, port), Handler)


def serve(port=8765, host="127.0.0.1"):
    httpd = make_server(port, host)
    print("Board editor on http://{}:{}/ (Ctrl-C stops)".format(host, port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def standalone_page(setup_id=None, board_id=None):
    """The editor page with its data inlined, read-only (./unifpga view)."""
    if setup_id:
        setups = su.read_setups()
        if setup_id not in setups:
            raise su.SetupError("no setup '{}'".format(setup_id))
        setup = setups[setup_id]
        board_id = setup["board"]
    else:
        setup = {"id": board_id, "board": board_id, "toolchain": "", "use": []}
    data = {"board": board_data(board_id), "setup": setup,
            "evaluation": evaluate(setup) if setup_id else {"problems": [], "trace": None}}
    with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as f:
        page = f.read()
    with open(os.path.join(STATIC, "studio.css"), encoding="utf-8") as f:
        css = f.read()
    with open(os.path.join(STATIC, "studio.js"), encoding="utf-8") as f:
        js = f.read()
    inline = json.dumps(data).replace("</", "<\\/")
    page = page.replace('<link rel="stylesheet" href="studio.css">', "<style>" + css + "</style>")
    page = page.replace('<script src="studio.js"></script>',
                        "<script>window.STUDIO_STATIC = " + inline + ";</script><script>" + js.replace("</script", "<\\/script") + "</script>")
    return page

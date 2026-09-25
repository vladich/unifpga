"""
Board sources: the registry of vendor documents a board's physical model is
checked against, and the facts read from them.

<registry>/<board>.yml, where <registry> is $UNIFPGA_SOURCES_DIR, by default the
sibling repository ../unifpga-board-sources (outside this one: it is the
evidence the physical model was checked against, not something a build or the
editor reads; generated layouts carry everything they need):

    BoardSources:
      board: de10_lite
      documents:
        - id: manual                      # referred to by the facts below
          title: DE10-Lite User Manual
          kind: manual                    # manual | schematic | constraints | pinout | board-files
          url: https://...
          access: ok                      # ok | manual-download | unavailable
          sha256: ...                     # written by `./unifpga sources fetch`
          bytes: 123456
          retrieved: 2026-09-23
      connector_types:                    # optional: types config/connectors.yml does not have
        terasic_gpio_2x20:
          name: Terasic 2x20 GPIO header
          voltage: 3.3
          rows: [[1, 3, 5, ...], [2, 4, 6, ...]]
          power: {11: VCC5, 12: GND, 29: VCC3P3, 30: GND}
          source: DE10-Lite User Manual, Figure 3-17
      headers:                            # one per header the pinmap has a bank for
        - bank: gpio                      # the pinmap bank
          id: jp1                         # the layout's connector id (default: the bank)
          label: GPIO header (JP1)
          type: terasic_gpio_2x20         # config/connectors.yml: rows, power pins, voltage
          pins: {1: V10, 2: W10, ...}     # physical pin -> FPGA pin, as the document prints it
          source: {doc: manual, where: "Table 3-14, p. 31"}
      onboard:                            # on-board parts the configurations attach
        - bank: onboard_leds
          label: LEDR0-LEDR9              # the silkscreen
          active: high                    # as the document shows the circuit
          pins: [A8, A9, ...]             # FPGA pins, as the document lists them
          source: {doc: manual, where: "Table 3-6, p. 26"}

Documents are downloaded into a cache outside the repository (vendor files are
not ours to redistribute): $UNIFPGA_SOURCES_CACHE, default
~/.cache/unifpga/board_sources/<board>/<document id>.<ext>. The registry keeps
their URL, size and SHA-256, so anyone can fetch the same bytes and check them.

verify() compares the facts with the board's pinmap (config/boards/...): every
pin of a header bank has to sit at exactly one physical pin, on a signal
position of the connector type; an on-board part's pins and active level have
to be the pinmap's. It changes nothing: a disagreement is a finding to look
into (the pinmap, the document or the fact may be wrong).
"""

import datetime
import hashlib
import os
import re
import subprocess

from config import init as config_init
from tools import codegen

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")
REPO_DIR = os.path.dirname(CONFIG_DIR)


def sources_dir():
    return os.environ.get("UNIFPGA_SOURCES_DIR") or \
        os.path.join(os.path.dirname(REPO_DIR), "unifpga-board-sources")
USER_AGENT = "unifpga-board-sources/1 (+https://github.com/vladich/unifpga)"


class SourcesError(Exception):
    pass


def cache_dir():
    return os.environ.get("UNIFPGA_SOURCES_CACHE") or os.path.join(os.path.expanduser("~"), ".cache", "unifpga", "board_sources")


def read_all():
    """{board: registry entry} for every <registry>/<board>.yml ({} without a registry)."""
    import yaml
    base = sources_dir()
    out = {}
    if not os.path.isdir(base):
        return out
    for name in sorted(os.listdir(base)):
        if not name.endswith(".yml"):
            continue
        with open(os.path.join(base, name), encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        entry = data.get("BoardSources")
        if entry and entry.get("board"):
            out[entry["board"]] = entry
    return out


def read(board_id):
    return read_all().get(board_id)


def registry_path(board_id):
    return os.path.join(sources_dir(), board_id + ".yml")


# ---------------------------------------------------------------------------
# documents
# ---------------------------------------------------------------------------

def _ext(url):
    m = re.search(r"\.([A-Za-z0-9]{2,5})(?:$|[?#])", url.rsplit("/", 1)[-1])
    return m.group(1).lower() if m else "bin"


def document_path(board_id, doc):
    return os.path.join(cache_dir(), board_id, "{}.{}".format(doc["id"], _ext(doc.get("url") or "")))


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fetch(board_id, doc, timeout=180):
    """Download a document into the cache (unless it is there with the recorded
    hash). Returns {path, sha256, bytes, fresh}. A recorded hash that the
    download does not match is an error: the vendor changed the file."""
    path = document_path(board_id, doc)
    if os.path.exists(path) and doc.get("sha256") and sha256(path) == doc["sha256"]:
        return {"path": path, "sha256": doc["sha256"], "bytes": os.path.getsize(path), "fresh": False}
    if doc.get("access") in ("manual-download", "unavailable"):
        if os.path.exists(path):
            got = sha256(path)
            if doc.get("sha256") and got != doc["sha256"]:
                raise SourcesError("{}: the file in the cache is not the recorded one".format(path))
            return {"path": path, "sha256": got, "bytes": os.path.getsize(path), "fresh": False}
        raise SourcesError("{} {}: {} — put the file at {}".format(
            board_id, doc["id"], "download it by hand" if doc.get("access") == "manual-download" else "not available",
            path))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    cmd = ["curl", "-fsSL", "--retry", "2", "--max-time", str(timeout), "-A", USER_AGENT, "-o", tmp, doc["url"]]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    if r.returncode != 0:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise SourcesError("{} {}: download failed ({})".format(board_id, doc["id"], r.stderr.strip() or r.returncode))
    with open(tmp, "rb") as f:
        head = f.read(512)
    if _ext(doc["url"]) == "pdf" and not head.startswith(b"%PDF"):
        os.remove(tmp)
        raise SourcesError("{} {}: the server answered with something other than a PDF".format(board_id, doc["id"]))
    got = sha256(tmp)
    if doc.get("sha256") and got != doc["sha256"]:
        os.remove(tmp)
        raise SourcesError("{} {}: the download's SHA-256 {} is not the recorded {}".format(board_id, doc["id"], got, doc["sha256"]))
    os.replace(tmp, path)
    return {"path": path, "sha256": got, "bytes": os.path.getsize(path), "fresh": True}


def record_fetch(board_id, doc_id, got):
    """Write sha256 / bytes / retrieved of one document into its registry file,
    leaving everything else of the file as it is."""
    path = registry_path(board_id)
    with open(path, encoding="utf-8") as f:
        lines = f.read().split("\n")
    start = next((k for k, l in enumerate(lines) if re.match(r"^\s*- id:\s*{}\s*$".format(re.escape(doc_id)), l)), None)
    if start is None:
        raise SourcesError("{}: no document '{}'".format(path, doc_id))
    indent = re.match(r"^(\s*)-", lines[start]).group(1) + "  "
    end = start + 1
    while end < len(lines) and lines[end].startswith(indent) and not lines[end].lstrip().startswith("- "):
        end += 1
    fields = {"sha256": got["sha256"], "bytes": got["bytes"], "retrieved": datetime.date.today().isoformat()}
    block = [l for l in lines[start + 1:end] if not re.match(r"^\s*(sha256|bytes|retrieved):", l)]
    block += ["{}{}: {}".format(indent, k, v) for k, v in fields.items()]
    lines[start + 1:end] = block
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def text(board_id, doc, pages=None):
    """[(page number, text)] of a fetched PDF (all pages, or `pages` as
    (first, last), 1-based), or [(1, text)] of a text document."""
    path = document_path(board_id, doc)
    if not os.path.exists(path):
        raise SourcesError("{} {}: not fetched (./unifpga sources fetch {})".format(board_id, doc["id"], board_id))
    # a PDF by its content: a download script's URL (Terasic's archive_download.pl)
    # names no extension
    with open(path, "rb") as f:
        is_pdf = f.read(5) == b"%PDF-"
    if not is_pdf:
        with open(path, encoding="utf-8", errors="replace") as f:
            return [(1, f.read())]
    import pypdf
    reader = pypdf.PdfReader(path)
    first, last = pages or (1, len(reader.pages))
    return [(k, reader.pages[k - 1].extract_text() or "") for k in range(max(1, first), min(last, len(reader.pages)) + 1)]


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------

def _norm_pin(p):
    """An FPGA pin as documents write it: PIN_V10, V10, "IO_L1P_T0_34 (V10)", 52."""
    s = str(p).strip().upper()
    m = re.search(r"\(([A-Z]{0,3}\d+)\)\s*$", s)
    if m:
        s = m.group(1)
    return re.sub(r"^PIN_", "", s)


def connector_types(entry):
    """The connector catalogue a registry entry's facts are read against: the
    project's types (config/connectors.yml and the ones layouts carry) plus the
    entry's own `connector_types`."""
    from tools import setup as su
    out = dict(su.read_connectors())
    for tid, ctype in ((entry or {}).get("connector_types") or {}).items():
        if tid in out and out[tid] != ctype:
            raise SourcesError("connector type '{}' is defined differently elsewhere".format(tid))
        out[tid] = ctype
    return out


def bank_pins(pinmap, bank):
    """The bank's pins as [(ref, FPGA pin)] in pinmap order."""
    return [(ref, _norm_pin(pin)) for ref, pin in codegen._bind_pins(pinmap, bank) if pin]


def verify(board_id, entry=None, connectors=None):
    """{"headers": {bank: [problem]}, "onboard": {bank: [problem]},
    "documents": [problem], "info": [note]} — every list empty means the
    facts and the pinmap agree."""
    entry = entry or read(board_id)
    if entry is None:
        raise SourcesError("no registry entry {}".format(registry_path(board_id)))
    if connectors is None:
        connectors = connector_types(entry)
    pinmap = config_init.read_board_pinmap(board_id) or {}
    banks = pinmap.get("pinBanks") or {}
    docs = {d["id"]: d for d in entry.get("documents") or []}
    out = {"headers": {}, "onboard": {}, "documents": [], "info": []}
    for d in docs.values():
        if not d.get("url"):
            out["documents"].append("{}: no url".format(d["id"]))
        if d.get("access", "ok") == "ok" and not d.get("sha256"):
            out["documents"].append("{}: not fetched yet (no sha256)".format(d["id"]))

    def cite(fact, problems):
        src = fact.get("source") or {}
        if src.get("doc") not in docs:
            problems.append("no source document (source.doc names none of the registry's documents)")
        elif not src.get("where"):
            problems.append("the source says where in {} (page / table)".format(src.get("doc")))

    for h in entry.get("headers") or []:
        bank, problems = h.get("bank"), []
        out["headers"][bank] = problems
        cite(h, problems)
        if bank not in banks:
            problems.append("the pinmap has no bank '{}'".format(bank))
            continue
        ctype = connectors.get(h.get("type"))
        if ctype is None:
            problems.append("unknown connector type '{}' (config/connectors.yml)".format(h.get("type")))
            continue
        numbered = {str(k) for row in ctype.get("rows") or [] for k in row}
        power = {str(k) for k in (ctype.get("power") or {})}
        where = {}
        for phys, pin in (h.get("pins") or {}).items():
            phys = str(phys)
            if numbered and phys not in numbered:
                problems.append("pin {} is not a position of {}".format(phys, h["type"]))
            if phys in power:
                problems.append("pin {} is {} on {}, not a signal".format(phys, ctype["power"].get(phys, ctype["power"].get(int(phys) if phys.isdigit() else phys)), h["type"]))
            where.setdefault(_norm_pin(pin), []).append(phys)
        for fpga, physs in where.items():
            if len(physs) > 1:
                problems.append("FPGA pin {} is at header pins {}".format(fpga, ", ".join(physs)))
        for ref, fpga in bank_pins(pinmap, bank):
            if fpga not in where:
                problems.append("{} (FPGA {}) is at no pin of the header in the document".format(ref, fpga))
        extra = set(where) - {fpga for _r, fpga in bank_pins(pinmap, bank)}
        if extra:
            out["info"].append("{}: header pins with FPGA pins the pinmap bank does not have: {}".format(
                bank, ", ".join(sorted(extra))))
    for o in entry.get("onboard") or []:
        bank, problems = o.get("bank"), []
        out["onboard"][bank] = problems
        cite(o, problems)
        if bank not in banks:
            problems.append("the pinmap has no bank '{}'".format(bank))
            continue
        have = [fpga for _r, fpga in bank_pins(pinmap, bank)]
        if o.get("pins") is not None:
            want = [_norm_pin(p) for p in o["pins"]]
            if want != have:
                problems.append("the document's pins {} are not the pinmap's {}".format(want, have))
        if o.get("active"):
            pm_active = (banks[bank] or {}).get("active", "high") if isinstance(banks[bank], dict) else "high"
            if o["active"] != pm_active:
                problems.append("the document shows it active {}, the pinmap says {}".format(o["active"], pm_active))
    return out


def verified_items(board_id, entry=None, connectors=None):
    """(banks of headers, banks of on-board parts) whose facts verify."""
    entry = entry or read(board_id)
    if not entry:
        return set(), set()
    v = verify(board_id, entry, connectors)
    return ({b for b, p in v["headers"].items() if not p and not v["documents"]},
            {b for b, p in v["onboard"].items() if not p and not v["documents"]})

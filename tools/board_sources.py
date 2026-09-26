"""
Board sources: the documents a board's physical model is checked against and
the facts read from them, in the board's own file
(config/boards/<producer>/<family>/<id>.yml; config/schema/board.schema.json):

    documents:                      # after the catalogue fields
      - id: manual                  # what a `source:` refers to
        title: DE10-Lite User Manual
        kind: manual                # manual | schematic | constraints | pinout | platform | wiki | example | board-files | other
        url: https://...
        access: ok                  # ok (the default) | manual-download | unavailable
        sha256: ...                 # written by `./unifpga sources fetch`
        bytes: 123456
        retrieved: '2026-09-23'
        cache: ~/.cache/...         # a copy fetched by hand, when not at the cache's default path
    banks:
      onboard_leds:
        pins: [A8, A9, ...]
        source: {doc: manual, where: "Table 3-6, p. 26"}    # where the pins were read
    headers:                        # the drawn section
      - id: gpio
        type: terasic_gpio_2x20
        bank: gpio
        pins:                            # physical pin -> bank pin
          '1': gpio[0]
          '2': gpio[1]
          ...
        source:                          # a fact: the drafter keeps it, verify() checks it
          doc: manual
          where: Table 3-14, p. 31
    parts:
      - id: leds
        label: LEDR0-LEDR9
        source:
          doc: manual
          where: Table 3-5, p. 28

Documents are downloaded into a cache outside the repository (vendor files are
not ours to redistribute): $UNIFPGA_SOURCES_CACHE, default
~/.cache/unifpga/board_sources/<board>/<document id>.<ext>. The board file
keeps their URL, size and SHA-256, so anyone can fetch the same bytes and
check them.

verify() compares the facts with the board's banks: a header with a source
must be of a known connector type, every physical pin a signal position of
it, and every pin of its bank at exactly one physical pin; every source must
name one of the board's documents and say where. It changes nothing: a
disagreement is a finding to look into (the banks, the document or the fact
may be wrong). ./unifpga check runs it for every board (rule
board_provenance); ./unifpga sources verify prints it.
"""

import datetime
import hashlib
import os
import re
import subprocess

from config import init as config_init
from tools import codegen

USER_AGENT = "unifpga-board-sources/1 (+https://github.com/vladich/unifpga)"


class SourcesError(Exception):
    pass


def cache_dir():
    return os.environ.get("UNIFPGA_SOURCES_CACHE") or os.path.join(os.path.expanduser("~"), ".cache", "unifpga", "board_sources")


def boards_with_documents():
    """{board id: board} for every board whose file lists documents."""
    return {b: bd for b, bd in config_init.read_boards().items() if bd.get("documents")}


def documents(board):
    """{document id: document} of a board (the dict of config.init.read_board)."""
    return {d["id"]: d for d in (board or {}).get("documents") or [] if isinstance(d, dict) and d.get("id")}


# ---------------------------------------------------------------------------
# documents
# ---------------------------------------------------------------------------

def _ext(url):
    m = re.search(r"\.([A-Za-z0-9]{2,5})(?:$|[?#])", url.rsplit("/", 1)[-1])
    return m.group(1).lower() if m else "bin"


def document_path(board_id, doc):
    """Where a document's copy lives: its `cache` when the file says so, else
    the cache's default path."""
    if doc.get("cache"):
        return os.path.expanduser(doc["cache"])
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
    download does not match is reported, not overwritten silently."""
    path = document_path(board_id, doc)
    if os.path.exists(path) and doc.get("sha256") and sha256(path) == doc["sha256"]:
        return {"path": path, "sha256": doc["sha256"], "bytes": os.path.getsize(path), "fresh": False}
    if doc.get("access", "ok") != "ok":
        if os.path.exists(path):
            return {"path": path, "sha256": sha256(path), "bytes": os.path.getsize(path), "fresh": False}
        raise SourcesError("{} {}: {} — put the file at {} by hand".format(board_id, doc["id"], doc.get("access"), path))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    cmd = ["curl", "-fsSL", "--retry", "2", "--max-time", str(timeout), "-A", USER_AGENT, "-o", tmp, doc["url"]]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise SourcesError("{} {}: download failed ({})".format(board_id, doc["id"], r.stderr.decode(errors="replace").strip()[:200]))
    os.replace(tmp, path)
    return {"path": path, "sha256": sha256(path), "bytes": os.path.getsize(path), "fresh": True}


def record_fetch(board_id, doc_id, got):
    """Write sha256 / bytes / retrieved of one document into the board's file,
    leaving everything else of the file as it is."""
    board = config_init.read_board(board_id)
    if board is None:
        raise SourcesError("no board '{}'".format(board_id))
    path = board["_path"]
    with open(path, encoding="utf-8") as f:
        lines = f.read().split("\n")
    start = next((k for k, l in enumerate(lines) if re.match(r"^\s*- id:\s*{}\s*$".format(re.escape(doc_id)), l)), None)
    if start is None:
        raise SourcesError("{}: no document '{}'".format(path, doc_id))
    indent = re.match(r"^(\s*)-", lines[start]).group(1) + "  "
    end = start + 1
    while end < len(lines) and lines[end].startswith(indent) and not lines[end].lstrip().startswith("- "):
        end += 1
    fields = {"sha256": got["sha256"], "bytes": got["bytes"], "retrieved": "'{}'".format(datetime.date.today().isoformat())}
    block = [l for l in lines[start + 1:end] if not re.match(r"^\s*(sha256|bytes|retrieved):", l)]
    block += ["{}{}: {}".format(indent, k, v) for k, v in fields.items()]
    lines[start + 1:end] = block
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    config_init.clear_cache()


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
    s = str(p).strip()
    m = re.search(r"\(([A-Za-z]+\d+|\d+)\)\s*$", s)
    if m:
        s = m.group(1)
    s = re.sub(r"^PIN_", "", s, flags=re.I)
    return s.upper()


def bank_pins(board, bank):
    """The bank's pins as [(ref, FPGA pin)] in board order."""
    return [(ref, _norm_pin(pin)) for ref, pin in codegen._bind_pins(board, bank) if pin]


def part_bank(part):
    """The bank a drawn part sits on: its attach's (or first variant's) first
    bound bank, or its device's bank."""
    if isinstance(part.get("device"), dict):
        return part["device"].get("bank")
    attach = part.get("attach") or ((part.get("variants") or [{}])[0].get("attach") or {})
    banks = sorted({re.split(r"[.\[]", str(r))[0] for v in (attach.get("bind") or {}).values()
                    for r in (v if isinstance(v, list) else [v])})
    return banks[0] if banks else None


def verify(board, connectors=None):
    """{"documents": [problem], "headers": {bank: [problem]}, "parts": {bank:
    [problem]}, "info": [note]} for a board dict (config.init.read_board):
    every list empty means the facts and the banks agree. Only headers and
    parts with a `source` are facts; the others are drafts and not checked."""
    from tools import setup as su
    board_id = board["id"]
    if connectors is None:
        connectors = su.read_connectors()
    banks = board.get("banks") or {}
    docs = documents(board)
    out = {"documents": [], "headers": {}, "parts": {}, "info": []}
    for d in docs.values():
        if not d.get("url"):
            out["documents"].append("{}: no url".format(d["id"]))
        if d.get("access", "ok") == "ok" and not d.get("sha256"):
            out["documents"].append("{}: not fetched yet (no sha256)".format(d["id"]))

    def cite(what, src, problems):
        if not isinstance(src, dict) or src.get("doc") not in docs:
            problems.append("{}: its source names none of the board's documents".format(what))
        elif not src.get("where"):
            problems.append("{}: the source says where in {} (page / table)".format(what, src.get("doc")))

    for bank_name, bank in banks.items():
        if isinstance(bank, dict) and bank.get("source"):
            problems = []
            cite("bank " + bank_name, bank["source"], problems)
            if problems:
                out["parts"].setdefault(bank_name, []).extend(problems)
    for h in board.get("headers") or []:
        if not h.get("source"):
            continue
        bank, problems = h.get("bank"), []
        out["headers"][bank] = problems
        cite("header " + str(h.get("id")), h["source"], problems)
        if bank not in banks:
            problems.append("the board has no bank '{}'".format(bank))
            continue
        ctype = connectors.get(h.get("type"))
        if ctype is None:
            problems.append("unknown connector type '{}' (config/connectors.yml or the board's connector_types)".format(h.get("type")))
            continue
        numbered = {str(k) for row in ctype.get("rows") or [] for k in row}
        power = {str(k): v for k, v in (ctype.get("power") or {}).items()}
        where = {}
        for phys, ref in (h.get("pins") or {}).items():
            phys = str(phys)
            if numbered and phys not in numbered:
                problems.append("pin {} is not a position of {}".format(phys, h["type"]))
            if phys in power:
                problems.append("pin {} is {} on {}, not a signal".format(phys, power[phys], h["type"]))
            fpga = [_norm_pin(p) for _b, p in codegen._bind_pins(board, str(ref)) if p]
            if len(fpga) != 1:
                problems.append("pin {}: {!r} is not one pin of the board".format(phys, ref))
                continue
            where.setdefault(fpga[0], []).append(phys)
        for fpga, physs in where.items():
            if len(physs) > 1:
                problems.append("FPGA pin {} is at header pins {}".format(fpga, ", ".join(physs)))
        for ref, fpga in bank_pins(board, bank):
            if fpga not in where:
                problems.append("{} (FPGA {}) is at no pin of the header".format(ref, fpga))
        extra = set(where) - {fpga for _r, fpga in bank_pins(board, bank)}
        if extra:
            out["info"].append("{}: header pins on FPGA pins the bank does not have: {}".format(bank, ", ".join(sorted(extra))))
    for o in board.get("parts") or []:
        if not o.get("source"):
            continue
        bank = part_bank(o)
        problems = out["parts"].setdefault(bank, [])
        cite("part " + str(o.get("id")), o["source"], problems)
        if bank not in banks:
            problems.append("the board has no bank '{}'".format(bank))
    return out


def verified_items(board, connectors=None):
    """(banks of headers, banks of parts) whose facts verify."""
    v = verify(board, connectors)
    return ({b for b, p in v["headers"].items() if not p and not v["documents"]},
            {b for b, p in v["parts"].items() if not p and not v["documents"]})

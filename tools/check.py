"""
Schema and relationship check of the configuration (./unifpga check).

config/schema/entities.yml names every entity: where its records are stored,
the JSON Schema (config/schema/<entity>.schema.json) its files satisfy and
its relationships to the other entities. check() reads every file, validates
it against its schema, registers each record's identity, resolves every
`from` reference to an id of its `to` entity and runs the named rules (the
checks a reference table cannot express: a port map naming its peripheral's
own signals, a rig expanding on its board). The result is a report of
findings; the CLI prints it, tools/catalog_candidate.py embeds it for the
documents of an exact Git revision (rules that need the working tree are
skipped there and listed).

A finding is {code, path, detail, severity, entity}: `schema` (a file does
not satisfy its schema), `invalid_root` / `invalid_record` / `invalid_identity`
/ `filename_identity` / `duplicate_identity` (records and their ids),
`invalid_reference` / `unknown_reference` (relationships), the rule's name
(a rule's own finding), `missing_entity` (an entity with no file), `yaml` (a
file that does not parse), `unsupported_path` (a YAML file no entity stores).
"""

import fnmatch
import json
import os
import re
import sys

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from config import init as config_init            # noqa: E402
from config.references import parse_versioned_ref  # noqa: E402

SCHEMA_DIR = os.path.join(REPO, "config", "schema")
ENTITIES_FILE = os.path.join(SCHEMA_DIR, "entities.yml")
SCOPE = "schema-and-relations/v1"
MAX_FINDINGS = 10000
MAX_DETAIL = 512
CONTEXT_NAMES = ("clk", "rst", "rst_n", "clk_mhz", "diff_buf")   # context.<name> a driver may take
RESET_FLAGS = ("power_up", "pll_lock", "bank", "pin")            # reset sources besides the capability's


class CheckError(Exception):
    """The check itself cannot run (a missing library, a broken registry)."""


# ---------------------------------------------------------------------------
# the registry and the schemas
# ---------------------------------------------------------------------------

def read_entities(path=ENTITIES_FILE):
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or not isinstance(data.get("Entities"), dict):
        raise CheckError("{}: needs an Entities mapping".format(path))
    return data["Entities"]


def read_schema(name):
    with open(os.path.join(SCHEMA_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def _jsonschema():
    try:
        import jsonschema
        from jsonschema import exceptions
    except ImportError:
        raise CheckError("jsonschema is not installed for {}: {} -m pip install -r requirements.txt"
                         .format(sys.executable, sys.executable))
    return jsonschema.Draft202012Validator, exceptions.best_match


def validators(entities):
    """{entity: a validator of its schema}; the schemas themselves are checked."""
    Validator, _best = _jsonschema()
    out = {}
    for name, spec in entities.items():
        if spec.get("schema"):
            schema = read_schema(spec["schema"])
            Validator.check_schema(schema)
            out[name] = Validator(schema)
    return out


# ---------------------------------------------------------------------------
# files
# ---------------------------------------------------------------------------

def _matches(pattern, path):
    """Glob match segment by segment (`*` stays within one directory)."""
    p, q = pattern.split("/"), path.split("/")
    return len(p) == len(q) and all(fnmatch.fnmatchcase(b, a) for a, b in zip(p, q))


def _path_id(pattern, path):
    """The id a file's path gives a record without an id field: the segments
    the glob's wildcards matched, the extension dropped (designs/x/fileset.yml
    -> x; config/chips/p/f.yml -> p/f)."""
    parts = [b for a, b in zip(pattern.split("/"), path.split("/")) if "*" in a]
    if not parts:                                   # one fixed file: named after it
        parts = [path.rsplit("/", 1)[-1]]
    if parts[-1].endswith(".yml"):
        parts[-1] = parts[-1][:-4]
    return "/".join(parts)


def repository_documents(entities, repo=REPO):
    """Every file an entity stores, read: ({repo-relative path: document},
    [(path, error)]); a YAML file under config/ that no entity stores is
    included so it is reported."""
    patterns = [spec["files"] for spec in entities.values()]
    paths = set()
    for pattern in patterns:
        base = pattern.split("*")[0].rstrip("/")
        base_dir = os.path.join(repo, base) if base and not base.endswith(".yml") else os.path.join(repo, os.path.dirname(base))
        if pattern.count("*") == 0:
            if os.path.exists(os.path.join(repo, pattern)):
                paths.add(pattern)
            continue
        for dirpath, _dirs, files in os.walk(base_dir):
            for name in files:
                rel = os.path.relpath(os.path.join(dirpath, name), repo).replace(os.sep, "/")
                if _matches(pattern, rel):
                    paths.add(rel)
    for dirpath, dirs, files in os.walk(os.path.join(repo, "config")):
        dirs[:] = [d for d in dirs if not d.startswith("__")]
        for name in files:
            if name.endswith(".yml"):
                paths.add(os.path.relpath(os.path.join(dirpath, name), repo).replace(os.sep, "/"))
    documents, errors = {}, []
    for rel in sorted(paths):
        try:
            documents[rel] = config_init._read_yaml_file(os.path.join(repo, rel))
        except config_init.ConfigError as exc:
            errors.append((rel, str(exc)))
    return documents, errors


# ---------------------------------------------------------------------------
# the check
# ---------------------------------------------------------------------------

class _Context(object):
    """What a rule sees: every entity's records, the documents, the repository."""

    def __init__(self, entities, documents, records, repo, has_repo, finding):
        self.entities = entities
        self.documents = documents
        self.records = records      # {entity: {id: (path, record)}}
        self.repo = repo
        self.has_repo = has_repo
        self.finding = finding
        self.rule = None            # the rule being run, and its entity
        self.entity = None

    def record(self, entity, rec_id):
        hit = self.records.get(entity, {}).get(rec_id)
        return hit[1] if hit else None

    def fail(self, path, detail):
        """A finding of the rule being run, under the rule's name."""
        self.finding(self.rule, path, detail, self.entity)


def _walk(value, segments, pointer=""):
    """Yield (json pointer, value) for a dotted path: `*` every item of a
    list or value of a mapping, `*keys` every key of a mapping."""
    if not segments:
        yield pointer, value
        return
    head, rest = segments[0], segments[1:]
    if head == "*":
        if isinstance(value, list):
            for i, v in enumerate(value):
                for hit in _walk(v, rest, "{}/{}".format(pointer, i)):
                    yield hit
        elif isinstance(value, dict):
            for k, v in value.items():
                for hit in _walk(v, rest, "{}/{}".format(pointer, k)):
                    yield hit
    elif head == "*keys":
        if isinstance(value, dict):
            for k in value:
                for hit in _walk(k, rest, "{}/{}".format(pointer, k)):
                    yield hit
    elif isinstance(value, dict) and head in value:
        for hit in _walk(value[head], rest, "{}/{}".format(pointer, head)):
            yield hit


def _records_of(spec, path, doc, finding, entity):
    """Yield (id, record, expected id from the file name or None) for a
    file's records as the entity spec locates them; malformed shapes are
    findings, not exceptions."""
    root = spec.get("root")
    kind = spec.get("records", "one")
    id_field = spec.get("id")
    expected = os.path.basename(path)[:-4] if spec.get("file_named_after_id") else None
    if root is None:
        if not isinstance(doc, dict):
            finding("invalid_root", path, "{}: the document must be a mapping".format(entity))
            return
        if id_field:
            yield doc.get(id_field), doc, expected
        else:
            yield _path_id(spec["files"], path), doc, None
        return
    node = doc
    for segment in root.split("."):                # dotted: a list nested in a record (Family.chips)
        node = node.get(segment) if isinstance(node, dict) else None
    if kind == "one":
        if not isinstance(node, dict):
            finding("invalid_root", path, "{} must be a mapping".format(root))
            return
        yield (node.get(id_field) if id_field else _path_id(spec["files"], path)), node, expected
    elif kind == "list":
        if not isinstance(node, list):
            finding("invalid_root", path, "{} must be a list".format(root))
            return
        for ordinal, item in enumerate(node, 1):
            if not isinstance(item, dict):
                finding("invalid_record", path, "{} item {} must be a mapping".format(root, ordinal))
                continue
            yield item.get(id_field), item, None
    else:
        if not isinstance(node, dict):
            finding("invalid_root", path, "{} must be a mapping".format(root))
            return
        for key, item in node.items():
            yield key, item, None


def _target_ids(entity, records):
    """The ids a reference to `entity` may name."""
    return set(records.get(entity, {}))


def _extract(value, fmt):
    """The target id a reference value names (None: absent), or raise ValueError."""
    if value is None:
        return None
    if fmt == "versioned":
        return parse_versioned_ref(value)[0]
    if fmt == "record_id":
        value = value.get("id") if isinstance(value, dict) else value
    if not isinstance(value, str) or not value.strip():
        raise ValueError("not an id")
    return value


def check(documents=None, root=None, repo=REPO, entities=None):
    """Check the repository (documents None) or a {path: document} map whose
    paths are relative to `root` (a catalog capture). Returns the report."""
    entities = entities if entities is not None else read_entities()
    has_repo = documents is None
    findings, load_errors = [], []
    if has_repo:
        documents, load_errors = repository_documents(entities, repo)
    else:
        documents = {("{}/{}".format(root, k) if root else k): v for k, v in documents.items()}

    def finding(code, path, detail, entity=None):
        if len(findings) == MAX_FINDINGS:
            findings.append({"code": "finding_limit", "path": "", "severity": "error", "entity": None,
                             "detail": "finding limit reached; the report is incomplete"})
        if len(findings) < MAX_FINDINGS:
            findings.append({"code": code, "path": path, "detail": detail[:MAX_DETAIL],
                             "severity": "error", "entity": entity})

    for path, error in load_errors:
        finding("yaml", path, error)

    checked = validators(entities)
    _Validator, best_match = _jsonschema()
    records = {name: {} for name in entities}
    summary = {}
    stored = set()
    for name, spec in entities.items():
        paths = sorted(p for p in documents if _matches(spec["files"], p))
        stored.update(paths)
        summary[name] = {"files": len(paths), "records": 0, "schema": spec.get("schema")}
        validator = checked.get(name)
        for path in paths:
            doc = documents[path]
            if validator is not None:
                for error in sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path)):
                    best = best_match([error])
                    where = "/".join(str(p) for p in best.absolute_path) or "(document)"
                    finding("schema", path, "{}: {}".format(where, best.message), name)
            for rec_id, record, expected in _records_of(spec, path, doc, lambda c, p, d: finding(c, p, d, name), name):
                if not isinstance(rec_id, str) or not rec_id.strip():
                    finding("invalid_identity", path, "{} needs a nonempty string {}".format(name, spec.get("id", "id")), name)
                    continue
                if expected is not None and rec_id != expected:
                    finding("filename_identity", path, "{} {!r} is in a file named {!r}".format(name, rec_id, expected), name)
                prior = records[name].get(rec_id)
                if prior is not None:
                    finding("duplicate_identity", path, "{} {!r} is also in {}".format(name, rec_id, prior[0]), name)
                    continue
                records[name][rec_id] = (path, record)
                summary[name]["records"] += 1
        if not paths and spec.get("schema") and spec["files"].startswith("config/"):
            finding("missing_entity", "", "no file of {} ({})".format(name, spec["files"]), name)
    unexamined = sorted(p for p in documents if p not in stored)
    for path in unexamined:
        finding("unsupported_path", path, "no entity of config/schema/entities.yml stores this file")

    ctx = _Context(entities, documents, records, repo, has_repo, finding)
    rules_skipped = []
    for name, spec in entities.items():
        for rel in spec.get("relations") or []:
            if "rule" in rel:
                rule = RULES.get(rel["rule"])
                if rule is None:
                    raise CheckError("{}: rule {!r} is not implemented".format(name, rel["rule"]))
                if rule.needs_repo and not has_repo:
                    rules_skipped.append("{}.{}".format(name, rel["rule"]))
                    continue
                ctx.rule, ctx.entity = rel["rule"], name
                for rec_id, (path, record) in sorted(records[name].items()):
                    rule(ctx, name, rec_id, record, path)
                continue
            targets = _target_ids(rel["to"], records)
            segments = rel["from"].split(".")
            fmt = rel.get("format", "plain")
            for rec_id, (path, record) in sorted(records[name].items()):
                for pointer, value in _walk(record, segments):
                    try:
                        target = _extract(value, fmt)
                    except ValueError:
                        finding("invalid_reference", path, "{} {!r} {}: {!r} is not a {} reference"
                                .format(name, rec_id, rel["from"], value, rel["to"]), name)
                        continue
                    if target is None:
                        continue
                    if target not in targets:
                        finding("unknown_reference", path, "{} {!r} {} refers to absent {} {!r}"
                                .format(name, rec_id, rel["from"] + (pointer if "*" in rel["from"] else ""), rel["to"], target), name)

    findings.sort(key=lambda row: (row["path"], row["code"], row["detail"]))
    return {"scope": SCOPE,
            "status": "failed" if findings else "ok",
            "entities": summary,
            "counts": {name: s["records"] for name, s in summary.items()},
            "unexamined": unexamined,
            "rules_skipped": rules_skipped,
            "findings": findings}


# ---------------------------------------------------------------------------
# rules: what a reference table cannot say
# ---------------------------------------------------------------------------

RULES = {}


def rule(name, needs_repo=False):
    def wrap(fn):
        fn.needs_repo = needs_repo
        RULES[name] = fn
        return fn
    return wrap


def _signal_names(record):
    return {s.get("name") for s in record.get("signals") or [] if isinstance(s, dict)}


_REF = re.compile(r"^(?:~ ?)?(pin|capability|context|clock|const|\$)\.?([A-Za-z_][A-Za-z0-9_.]*)?(?:\[[0-9]+(?::[0-9]+)?\])?$")


def _check_ref(ctx, entity, rec_id, record, path, value, where, provided):
    """A driver reference resolves within its peripheral: pin.<signal>,
    capability.<provided>.<its signal>, clock.<its clock>, $<its parameter>,
    context.<known name>."""
    if not isinstance(value, str):
        return
    m = _REF.match(value.strip())
    if not m:
        return                                    # the schema rejects the shape
    kind, rest = m.group(1), m.group(2) or ""
    if kind == "$":
        rest = value.strip().lstrip("~ ")[1:]
        kind = "param"
    parts = rest.split(".")
    names = _signal_names(record)
    if kind == "pin" and parts[0] not in names:
        ctx.fail(path, "{} {!r} {}: pin.{} is not one of its signals".format(entity, rec_id, where, parts[0]))
    elif kind == "capability":
        cap_id, sig = parts[0], "_".join(parts[1:])
        cap = ctx.record("capability", cap_id)
        if cap_id not in provided:
            ctx.fail(path, "{} {!r} {}: capability {} is not one it provides".format(entity, rec_id, where, cap_id))
        elif cap is not None and sig not in _signal_names(cap):
            ctx.fail(path, "{} {!r} {}: capability {} has no signal {}".format(entity, rec_id, where, cap_id, sig))
    elif kind == "clock":
        clocks = {c.get("name") for c in record.get("clocks") or [] if isinstance(c, dict)}
        if parts[0] not in clocks:
            ctx.fail(path, "{} {!r} {}: clock.{} is not one of its clocks".format(entity, rec_id, where, parts[0]))
    elif kind == "param":
        params = set(record.get("parameters") or {}) | set(record.get("derive") or {})
        if parts[0] not in params:
            ctx.fail(path, "{} {!r} {}: ${} is not one of its parameters".format(entity, rec_id, where, parts[0]))
    elif kind == "context" and parts[0] not in CONTEXT_NAMES:
        ctx.fail(path, "{} {!r} {}: context.{} is not one of {}".format(entity, rec_id, where, parts[0], ", ".join(CONTEXT_NAMES)))


@rule("peripheral_refs")
def _peripheral_refs(ctx, entity, rec_id, record, path):
    names = _signal_names(record)
    params = set(record.get("parameters") or {}) | set(record.get("derive") or {})
    clocks = {c.get("name") for c in record.get("clocks") or [] if isinstance(c, dict)}
    provided = {p.get("capability") for p in record.get("provides") or [] if isinstance(p, dict)}

    def bad(detail):
        ctx.fail(path, "{} {!r} {}".format(entity, rec_id, detail))

    for sig in record.get("signals") or []:
        if isinstance(sig.get("width"), str) and sig["width"][1:] not in params:
            bad("signal {}: width {} is not one of its parameters".format(sig.get("name"), sig["width"]))
    for pname, spec in (record.get("parameters") or {}).items():
        if isinstance(spec, dict) and spec.get("from_width") and spec["from_width"] not in names:
            bad("parameter {}: from_width {} is not one of its signals".format(pname, spec["from_width"]))
    for pname, spec in (record.get("derive") or {}).items():
        for operands in (spec or {}).values():
            for op in operands or []:
                if isinstance(op, str) and op not in params:
                    bad("derive {}: {} is not one of its parameters".format(pname, op))
    for entry in record.get("provides") or []:
        cap = ctx.record("capability", entry.get("capability"))
        cap_params = set((cap or {}).get("parameters") or {})
        cap_signals = _signal_names(cap) if cap else set()
        for k, v in (entry.get("params") or {}).items():
            if cap is not None and k not in cap_params:
                bad("provides {}: params.{} is not a parameter of the capability".format(entry.get("capability"), k))
            if isinstance(v, str) and v.startswith("$") and v[1:] not in params:
                bad("provides {}: params.{} = {} is not one of its parameters".format(entry.get("capability"), k, v))
        for k, v in (entry.get("signal_map") or {}).items():
            if cap is not None and k not in cap_signals:
                bad("provides {}: signal_map.{} is not a signal of the capability".format(entry.get("capability"), k))
            if v not in names:
                bad("provides {}: signal_map.{} = {} is not one of its signals".format(entry.get("capability"), k, v))
        when = entry.get("when")
        if isinstance(when, str) and when[1:] not in params:
            bad("provides {}: when {} is not one of its parameters".format(entry.get("capability"), when))
    driver = record.get("driver") or {}
    for port, value in (driver.get("port_map") or {}).items():
        for one in (value if isinstance(value, list) else [value]):
            _check_ref(ctx, entity, rec_id, record, path, one, "port_map.{}".format(port), provided)
    for pname, value in (driver.get("parameters") or {}).items():
        _check_ref(ctx, entity, rec_id, record, path, value, "driver.parameters.{}".format(pname), provided)
    for port in driver.get("port_format") or {}:
        if port not in (driver.get("port_map") or {}):
            bad("port_format.{} is not a port of the port map".format(port))
    for key, value in (record.get("pin_assigns") or {}).items():
        _check_ref(ctx, entity, rec_id, record, path, key, "pin_assigns", provided)
        _check_ref(ctx, entity, rec_id, record, path, value, "pin_assigns.{}".format(key), provided)
    for sig, served in (record.get("serves") or {}).items():
        if sig not in names:
            bad("serves.{} is not one of its signals".format(sig))
        for item in served or []:
            for cs in (item if isinstance(item, list) else [item]):
                cap_id, _sep, sname = str(cs).partition(".")
                cap = ctx.record("capability", cap_id)
                if cap_id not in provided:
                    bad("serves.{}: {} is not a capability it provides".format(sig, cs))
                elif cap is not None and sname not in _signal_names(cap):
                    bad("serves.{}: capability {} has no signal {}".format(sig, cap_id, sname))
    for sig in record.get("pin_fit") or {}:
        if sig not in names:
            bad("pin_fit.{} is not one of its signals".format(sig))
    kinds = ctx.records.get("kind", {})
    models = record.get("models") or {}
    model_kinds = models.get("kind") if isinstance(models, dict) else None
    for kind in (model_kinds if isinstance(model_kinds, list) else [model_kinds] if model_kinds else []):
        if kinds and kind not in kinds:
            bad("models: kind {!r} is not one of config/kinds.yml".format(kind))
    if isinstance(models, dict):
        if models.get("signal") and models["signal"] not in names:
            bad("models: signal {!r} is not one of its signals".format(models["signal"]))
        for sig in models.get("pins") or {}:
            if sig not in names:
                bad("models: pins.{} is not one of its signals".format(sig))
    for clock in record.get("clocks") or []:
        if clock.get("from") and clock["from"] not in clocks:
            bad("clock {}: from {} is not one of its clocks".format(clock.get("name"), clock["from"]))
        for k in clock.get("when") or {}:
            if k not in params:
                bad("clock {}: when.{} is not one of its parameters".format(clock.get("name"), k))
        if isinstance(clock.get("mhz"), str) and clock["mhz"][1:] not in params:
            bad("clock {}: mhz {} is not one of its parameters".format(clock.get("name"), clock["mhz"]))


@rule("board_kinds")
def _board_kinds(ctx, entity, rec_id, record, path):
    """Every bank's device kind is a kind of config/kinds.yml; the board lists
    one of the features the kind implies."""
    kinds = ctx.records.get("kind", {})
    if not kinds:
        return
    listed = set(record.get("features") or [])
    missing = {}
    for bank_name, bank in (record.get("banks") or {}).items():
        device = bank.get("device") if isinstance(bank, dict) else None
        if not isinstance(device, dict):
            continue
        kind = device.get("kind")
        if kind not in kinds:
            ctx.fail(path, "board {!r} bank {}: device kind {!r} is not one of config/kinds.yml".format(rec_id, bank_name, kind))
            continue
        implied = kinds[kind][1].get("features") or []
        if implied and not listed & set(implied):
            missing.setdefault((kind, tuple(implied)), []).append(bank_name)
    for (kind, implied), banks in sorted(missing.items()):
        ctx.fail(path, "board {!r}: bank{} {} ({}) impl{} one of the features {}, none is listed".format(
            rec_id, "s" if len(banks) > 1 else "", ", ".join(banks), kind, "y" if len(banks) > 1 else "ies", " / ".join(implied)))


@rule("producer_names_unique")
def _producer_names_unique(ctx, entity, rec_id, record, path):
    """A producer's name and akas name no other producer."""
    for name in [record.get("name")] + list(record.get("aka") or []):
        for other_id, (_p, other) in ctx.records.get("producer", {}).items():
            if other_id != rec_id and isinstance(other, dict) and name in [other.get("name"), other_id] + list(other.get("aka") or []):
                ctx.fail(path, "producer {!r}: {!r} also names producer {!r}".format(rec_id, name, other_id))


@rule("board_provenance", needs_repo=True)
def _board_provenance(ctx, entity, rec_id, record, path):
    """The board's documents and the facts read from them (tools/board_sources.py
    verify): documents fetched, every source naming a document and where in
    it, every header fact of a known connector type with every bank pin at
    one physical position."""
    from tools import board_sources as bs
    if not (record.get("documents") or any(h.get("source") for h in record.get("headers") or [])
            or any(o.get("source") for o in record.get("parts") or [])
            or any(isinstance(b, dict) and b.get("source") for b in (record.get("banks") or {}).values())):
        return
    v = bs.verify(record)
    for problem in v["documents"]:
        ctx.fail(path, "board {!r} document {}".format(rec_id, problem))
    for bank, problems in v["headers"].items():
        for problem in problems:
            ctx.fail(path, "board {!r} header on {}: {}".format(rec_id, bank, problem))
    for bank, problems in v["parts"].items():
        for problem in problems:
            ctx.fail(path, "board {!r} {}: {}".format(rec_id, bank, problem))


@rule("design_top_sections")
def _design_top_sections(ctx, entity, rec_id, record, path):
    """Every capability with a design block is in exactly one section."""
    listed = [c for sec in record.get("sections") or [] for c in sec.get("capabilities") or []]
    for c in sorted({c for c in listed if listed.count(c) > 1}):
        ctx.fail(path, "the capability {!r} is in two sections".format(c))
    for cid, (_p, cap) in sorted(ctx.records.get("capability", {}).items()):
        if isinstance(cap, dict) and cap.get("design") and cid not in listed:
            ctx.fail(path, "the capability {!r} puts ports on design_top but is in no section".format(cid))


@rule("design_top_interface", needs_repo=True)
def _design_top_interface(ctx, entity, rec_id, record, path):
    """rtl/peripherals/design_top_interface.sv is what the data renders to,
    and every design takes its module header from the rendered include
    (design_top_interface.svh, rendered by every build), not from a copy."""
    from tools import design_top
    try:
        if not design_top.is_current():
            ctx.fail(path, "rtl/peripherals/design_top_interface.sv is not what the capabilities render to: ./unifpga interface --write")
        for design in design_top.design_files():
            with open(design, encoding="utf-8") as f:
                text = f.read()
            if not design_top.includes_header(text):
                rel = os.path.relpath(design, ctx.repo)
                ctx.fail(path, "{} carries a hand-written module header instead of {}: ./unifpga interface --write {}"
                         .format(rel, design_top.DIRECTIVE, rel))
    except Exception as exc:                   # a capability the renderer cannot place
        ctx.fail(path, "the interface cannot be rendered: {}".format(exc))


@rule("peripheral_driver_files", needs_repo=True)
def _peripheral_driver_files(ctx, entity, rec_id, record, path):
    driver = record.get("driver") or {}
    for rel in [driver.get("file")] + list(driver.get("files") or []):
        if rel and not os.path.isfile(os.path.join(ctx.repo, rel)):
            ctx.fail(path, "{} {!r}: driver file {} does not exist".format(entity, rec_id, rel))


@rule("capability_refs")
def _capability_refs(ctx, entity, rec_id, record, path):
    names = _signal_names(record)
    params = set(record.get("parameters") or {})
    design = record.get("design") or {}
    design_params = set(design.get("parameters") or {}) | set(design.get("derived") or {})

    def bad(detail):
        ctx.fail(path, "{} {!r} {}".format(entity, rec_id, detail))

    for key in ("primary",):
        if record.get(key) and record[key] not in params:
            bad("{} {} is not one of its parameters".format(key, record[key]))
    for p in record.get("size") or []:
        if p not in params:
            bad("size: {} is not one of its parameters".format(p))
    for p in record.get("depth") or []:
        if p not in design_params:
            bad("depth: {} is not a design parameter".format(p))
    for sig in record.get("signals") or []:
        if sig.get("extent") and sig["extent"] not in params:
            bad("signal {}: extent {} is not one of its parameters".format(sig.get("name"), sig["extent"]))
        if isinstance(sig.get("width"), str) and sig["width"][1:] not in params:
            bad("signal {}: width {} is not one of its parameters".format(sig.get("name"), sig["width"]))
    for pname, spec in (design.get("parameters") or {}).items():
        value = spec.get("value")
        for alt in (value if isinstance(value, list) else [value]):
            name = alt if isinstance(alt, str) else (alt.get("param") if isinstance(alt, dict) and "param" in alt else None)
            if name is not None and name not in params:
                bad("design parameter {}: value {} is not one of its parameters".format(pname, name))
    for pname, spec in (design.get("derived") or {}).items():
        operands = [spec.get("clog2")] if "clog2" in spec else list(spec.get("multiply") or [])
        for op in operands:
            if isinstance(op, str) and op not in design_params:
                bad("derived {}: {} is not a design parameter".format(pname, op))
    for port, spec in (design.get("ports") or {}).items():
        if spec.get("signal") not in names:
            bad("port {}: signal {} is not one of its signals".format(port, spec.get("signal")))
        if isinstance(spec.get("width"), str) and spec["width"] not in design_params:
            bad("port {}: width {} is not a design parameter".format(port, spec["width"]))
    for sname, spec in (record.get("sources") or {}).items():
        cap = ctx.record("capability", spec.get("capability"))
        if cap is not None and spec.get("signal") not in _signal_names(cap):
            bad("source {}: capability {} has no signal {}".format(sname, spec.get("capability"), spec.get("signal")))


@rule("module_pins")
def _module_pins(ctx, entity, rec_id, record, path):
    perif = ctx.record("peripheral", record.get("peripheral"))
    if perif is None:
        return                                    # the relation reports it
    names = _signal_names(perif)
    params = set(perif.get("parameters") or {})
    pins = record.get("pins") or {}
    for label, role in pins.items():
        base = str(role).split("[")[0]
        if base not in ("power", "ground") and base not in names:
            ctx.fail(path, "module {!r} pin {}: {} is not a signal of {}".format(rec_id, label, role, record.get("peripheral")))
    for pname in record.get("params") or {}:
        if pname not in params:
            ctx.fail(path, "module {!r} params.{} is not a parameter of {}".format(rec_id, pname, record.get("peripheral")))
    for label in record.get("unwired") or {}:
        if label not in pins:
            ctx.fail(path, "module {!r} unwired.{} is not one of its pins".format(rec_id, label))


def _board_chips(board):
    """[(chip id, variant name or None)] of a board."""
    if board.get("chip"):
        return [(board["chip"], None)]
    return [(c.get("id"), c.get("name")) if isinstance(c, dict) else (c, None) for c in board.get("chips") or []]


@rule("rig_chip_variant")
def _rig_chip_variant(ctx, entity, rec_id, record, path):
    board = ctx.record("board", record.get("board"))
    if board is None:
        return
    chips = _board_chips(board)
    wanted = [record.get("part")] + list(record.get("parts") or []) + \
             [a.get("part") for a in (record.get("aliases") or {}).values() if isinstance(a, dict)]
    for part in wanted:
        if part is None:
            continue
        w = str(part).strip().lower()
        names = set()
        for cid, vname in chips:
            names |= {str(cid).lower(), str(vname or "").lower()}
        if w not in names:
            ctx.fail(path, "rig {!r}: part {!r} is not one of the board's chips ({})".format(
                rec_id, part, ", ".join(vname or cid for cid, vname in chips) or "none"))


@rule("rig_reset_sources")
def _rig_reset_sources(ctx, entity, rec_id, record, path):
    reset = ctx.record("capability", "reset")
    if reset is None:
        return
    allowed = set(reset.get("sources") or {}) | set(RESET_FLAGS)
    designs = [record.get("design") or {}] + \
              [(p or {}).get("design") or {} for p in (record.get("for_toolchain") or {}).values()]
    for design in designs:
        for src in (design.get("reset") or {}).get("sources") or []:
            for key in (src if isinstance(src, dict) else {}):
                if key not in allowed:
                    ctx.fail(path, "rig {!r}: reset source {!r} is not one of {}".format(
                        rec_id, key, ", ".join(sorted(allowed))))


@rule("rig_expands", needs_repo=True)
def _rig_expands(ctx, entity, rec_id, record, path):
    from tools import setup as su
    for level, message in su.validate(record):
        if level == "error":
            ctx.fail(path, "rig {!r}: {}".format(rec_id, message))
    try:
        su.generate(record)
    except su.SetupError as exc:
        ctx.fail(path, "rig {!r}: {}".format(rec_id, exc))


@rule("board_drawn")
def _board_drawn(ctx, entity, rec_id, record, path):
    """A board's headers are of a known connector type and sit on its banks;
    its parts bind its banks."""
    known = set(ctx.records.get("connector_type", {})) | set(record.get("connector_types") or {})
    banks = set(record.get("banks") or {})
    for i, header in enumerate(record.get("headers") or []):
        if not isinstance(header, dict):
            continue
        if header.get("type") not in known:
            ctx.fail(path, "board {!r} header {}: type {!r} is neither in config/connectors.yml nor the board's connector_types"
                     .format(rec_id, header.get("id", i), header.get("type")))
        if header.get("bank") and header["bank"] not in banks:
            ctx.fail(path, "board {!r} header {}: bank {!r} is not one of its banks".format(rec_id, header.get("id", i), header["bank"]))
    for part in record.get("parts") or []:
        if not isinstance(part, dict):
            continue
        attaches = [part.get("attach")] + [v.get("attach") for v in part.get("variants") or [] if isinstance(v, dict)]
        for attach in attaches:
            for sig, ref in ((attach or {}).get("bind") or {}).items():
                for one in (ref if isinstance(ref, list) else [ref]):
                    bank = re.split(r"[.\[]", str(one))[0]
                    if bank not in banks:
                        ctx.fail(path, "board {!r} part {}: {} binds {!r}, not one of its banks".format(rec_id, part.get("id"), sig, one))
        if "device" in part and isinstance(part["device"], dict) and part["device"].get("bank") not in banks:
            ctx.fail(path, "board {!r} part {}: device bank {!r} is not one of its banks".format(rec_id, part.get("id"), part["device"].get("bank")))


@rule("toolchain_driver", needs_repo=True)
def _toolchain_driver(ctx, entity, rec_id, record, path):
    if record.get("operations") and not os.path.isfile(os.path.join(ctx.repo, "toolchains", rec_id, rec_id + ".py")):
        ctx.fail(path, "toolchain {!r} has operations but no driver toolchains/{}/{}.py".format(rec_id, rec_id, rec_id))


@rule("design_fileset", needs_repo=True)
def _design_fileset(ctx, entity, rec_id, record, path):
    from tools import source_set
    try:
        source_set.design_inputs(os.path.dirname(os.path.join(ctx.repo, path)))
    except source_set.SourceSetError as exc:
        ctx.fail(path, "design {!r}: {}".format(rec_id, exc))


# ---------------------------------------------------------------------------
# the command
# ---------------------------------------------------------------------------

def render(report, only=None):
    """The report as ./unifpga check prints it."""
    lines = []
    width = max(len(n) for n in report["entities"]) + 2
    lines.append("{:<{w}} {:>5} {:>7}  schema".format("entity", "files", "records", w=width))
    for name, s in report["entities"].items():
        if only and name not in only:
            continue
        lines.append("{:<{w}} {:>5} {:>7}  {}".format(name, s["files"], s["records"], s["schema"] or "(none yet)", w=width))
    findings = [f for f in report["findings"] if not only or f.get("entity") in only or f.get("entity") is None]
    last = None
    for f in findings:
        if f["path"] != last:
            lines.append(f["path"] or "(catalogue)")
            last = f["path"]
        lines.append("    {}: {}".format(f["code"], f["detail"]))
    without = [n for n, s in report["entities"].items() if not s["schema"]]
    lines.append("{} finding{} in {} file{}; {} entit{} without a schema yet{}".format(
        len(findings), "" if len(findings) == 1 else "s", len({f["path"] for f in findings}),
        "" if len({f["path"] for f in findings}) == 1 else "s",
        len(without), "y" if len(without) == 1 else "ies", ": " + ", ".join(without) if without else ""))
    if report.get("rules_skipped"):
        lines.append("rules not run without the repository: " + ", ".join(report["rules_skipped"]))
    return "\n".join(lines) + "\n"


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Check every configuration file against its schema and every reference between them.")
    parser.add_argument("entities", nargs="*", help="only these entities (default: all)")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)
    try:
        report = check()
    except CheckError as exc:
        print("check: {}".format(exc), file=sys.stderr)
        return 2
    unknown = [e for e in args.entities if e not in report["entities"]]
    if unknown:
        print("check: unknown entity {} (one of {})".format(", ".join(unknown), ", ".join(report["entities"])), file=sys.stderr)
        return 2
    if args.json:
        json.dump(report, sys.stdout, indent=1, sort_keys=True)
        print()
    else:
        sys.stdout.write(render(report, set(args.entities) or None))
    return 1 if report["status"] == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())

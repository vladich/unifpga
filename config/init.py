#!/usr/bin/env python3
"""
Configuration loader.

A *rig* (its setup, config/setups/<id>.yml; the configuration is its expansion) is the unit of selection.
Each configuration declares a board + toolchain + a list of attached
peripherals. Picking a configuration resolves to a fully-loaded object that
synthesize.py and toolchain modules consume.
"""

import contextlib
import copy
import hashlib
import json
import os
import re
import sys
import logging

import yaml

if __package__:
    from .references import parse_versioned_ref as _parse_versioned_ref
else:  # direct execution of config/init.py
    from references import parse_versioned_ref as _parse_versioned_ref


log = logging.getLogger(__name__)
dir_path = os.path.dirname(os.path.realpath(__file__))


class ConfigError(Exception):
    """Raised when a configuration file is missing or malformed."""


# ---------------------------------------------------------------------------
# Parsed-YAML cache
#
# resolve_configuration() and the codegen call the read_* functions many times
# per run (and the test-suite calls them ~134 times each). Parsing ~300 YAML
# files on every call made one resolve take seconds. We cache the parsed
# document per file, keyed on (mtime, size), and hand out deep copies so
# callers that annotate the dicts (resolve_configuration selects a board's
# chip and applies a rig's overrides to its banks) never leak state into each
# other.
# ---------------------------------------------------------------------------

_yaml_cache = {}
# libyaml's safe loader when PyYAML has it: the same documents (checked on
# every file under config/), about ten times faster than the pure-Python one
_SAFE_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


class _UniqueKeyLoader(_SAFE_LOADER):
    """Reject duplicate authored mapping keys before PyYAML overwrites them."""

    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            # YAML merge keys are handled by the base loader. An explicit key
            # may intentionally override a merged default, but two explicit
            # spellings of the same key are never unambiguous source data.
            if key_node.tag == "tag:yaml.org,2002:merge":
                continue
            key = self.construct_object(key_node, deep=deep)
            try:
                hash(key)
            except TypeError as exc:
                raise yaml.constructor.ConstructorError(
                    "while reading a mapping", node.start_mark,
                    "unhashable key", key_node.start_mark) from exc
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    "while reading a mapping", node.start_mark,
                    "duplicate key {!r}".format(key), key_node.start_mark)
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def _parsed(path):
    """The cached parse of `path` itself, NOT a copy: for read-only indexes
    built here, never handed to callers."""
    _read_yaml_file(path)
    return _yaml_cache[path][1]


def _read_yaml_file(path):
    """Parse `path` with yaml.safe_load, cached on the file's mtime and size.
    Raises ConfigError on YAML errors. Returns a deep copy of the document."""
    try:
        st = os.stat(path)
    except OSError as exc:
        raise ConfigError("Config file not found: {p} ({e})".format(p=path, e=exc))
    key = (st.st_mtime_ns, st.st_size)
    cached = _yaml_cache.get(path)
    if cached is None or cached[0] != key:
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.load(f, Loader=_UniqueKeyLoader)
        except yaml.YAMLError as exc:
            raise ConfigError("YAML parse error in {p}: {e}".format(p=path, e=exc))
        cached = (key, data)
        _yaml_cache[path] = cached
    return copy.deepcopy(cached[1])


def clear_cache():
    """Drop every cached document (tests that rewrite config files call this)."""
    _yaml_cache.clear()
    _CONFIGURATIONS.clear()
    _BOARDS.clear()


def _load_yaml(path, root_key):
    if not os.path.exists(path):
        raise ConfigError("Config file not found: {p}".format(p=path))
    data = _read_yaml_file(path)
    if data is None or root_key not in data:
        raise ConfigError("Missing root element '{k}' in {p}".format(k=root_key, p=path))
    return data[root_key]


def _load_yaml_dir(subdir, root_key, id_key, *, base=None, missing_ok=False):
    """Iterate config/<subdir>/*.yml and return {id: parsed_root}."""
    base = base or os.path.join(dir_path, subdir)
    if not os.path.isdir(base):
        if missing_ok:
            return {}
        raise ConfigError("Directory not found: " + base)
    out = {}
    origins = {}
    for fname in sorted(os.listdir(base)):
        if not fname.endswith(".yml") or fname.startswith("_"):
            continue
        path = os.path.join(base, fname)
        data = _read_yaml_file(path)
        if not isinstance(data, dict) or root_key not in data:
            raise ConfigError("{}: missing '{}' mapping".format(path, root_key))
        item = data[root_key]
        if (not isinstance(item, dict) or not isinstance(item.get(id_key), str) or
                not item[id_key].strip()):
            raise ConfigError("{}: '{}' must contain a nonempty string '{}'"
                              .format(path, root_key, id_key))
        identifier = item[id_key]
        if identifier in out:
            raise ConfigError("duplicate {} {!r}: {} and {}".format(
                root_key, identifier, origins[identifier], path))
        out[identifier] = item
        origins[identifier] = path
    return out


def _unique_catalog(items, source, kind, key="id"):
    """Index a single-file registry without losing malformed or repeated IDs."""
    if not isinstance(items, list):
        raise ConfigError("{}: {} must be a list".format(source, kind))
    out = {}
    for ordinal, item in enumerate(items, 1):
        if (not isinstance(item, dict) or not isinstance(item.get(key), str) or
                not item[key].strip()):
            raise ConfigError("{}: {} item {} needs a nonempty string {}".format(
                source, kind, ordinal, key))
        if item[key] in out:
            raise ConfigError("{}: duplicate {} {} {!r}".format(source, kind, key, item[key]))
        out[item[key]] = item
    return out


def read_toolchains():
    """Read the list of toolchains from toolchains.yml."""
    items = _load_yaml(os.path.join(dir_path, "toolchains.yml"), "Toolchains")
    indexed = _unique_catalog(items, os.path.join(dir_path, "toolchains.yml"), "Toolchain")
    for toolchain in indexed.values():
        supported_operations(toolchain)
    return indexed


def supported_operations(toolchain):
    """Validate the explicit list of executable driver operations.

    A catalogue entry may describe a chip/tool even before its driver exists.
    That description must never be treated as executable support by default.
    """
    operations = toolchain.get("operations")
    if not isinstance(operations, list) or any(
            not isinstance(op, str) or op not in ("synthesize", "program")
            for op in operations) or len(operations) != len(set(operations)):
        raise ConfigError("Toolchain '{t}' needs a unique operations list "
                          "containing only synthesize and/or program"
                          .format(t=toolchain.get("id", "?")))
    return operations


def require_toolchain_operation(toolchain, operation):
    """Reject a catalogue-only driver before a build or board action starts."""
    if operation not in supported_operations(toolchain):
        raise ConfigError("Toolchain '{t}' does not implement {op}; choose a "
                          "supported toolchain or implement its driver"
                          .format(t=toolchain.get("id", "?"), op=operation))


def board_fingerprint(board):
    """Digest of the exact board data the constraints are generated from — its
    id, banks (with the configuration's pin and I/O overrides applied),
    defaults and toolchain_options — so an attestation of the base board
    cannot accidentally authorize a different rig pinout."""
    facts = {k: board.get(k) for k in ("id", "banks", "defaults", "toolchain_options") if k in board}
    try:
        encoded = json.dumps(facts, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ConfigError("Board {!r} cannot be fingerprinted: {}".format(board.get("id"), exc))
    return hashlib.sha256(encoded).hexdigest()


def require_hardware_readiness(board):
    """Admit physical builds only with review of the exact resolved banks.

    The YAML attestation (the board's `verification`) is a review record, not
    a claim that this process can independently verify a vendor schematic.
    Missing records fail closed.
    """
    board_id = board.get("id", "?")
    verification = board.get("verification")
    if not isinstance(verification, dict) or verification.get("status") != "verified":
        raise ConfigError("Board '{}' is not verified for hardware; "
                          "use UNIFPGA_DRY_RUN=1 to inspect generated files".format(board_id))
    digest = verification.get("banks_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or \
            digest != board_fingerprint(board):
        raise ConfigError("Board '{}' verification digest is missing or stale".format(board_id))
    selected_part = board.get("chip_id") or board.get("part")
    parts = verification.get("parts")
    if not isinstance(parts, list) or not parts or any(not isinstance(p, str) or not p for p in parts) \
            or len(parts) != len(set(parts)) or selected_part not in parts:
        raise ConfigError("Board '{}' verification does not cover selected part '{}'"
                          .format(board_id, selected_part))
    for subject in ("pinout", "electrical"):
        evidence = verification.get(subject)
        if not isinstance(evidence, dict) or any(
                not isinstance(evidence.get(field), str) or not evidence[field].strip()
                for field in ("source", "revision")):
            raise ConfigError("Board '{}' verification lacks {} source and revision"
                              .format(board_id, subject))


def read_programmers():
    """Read the registry of programmers from programmers.yml.

    A programmer is a tool that loads a bitstream onto a physical board
    (over JTAG, USB-DFU, SPI, or board-specific bootloader). It is
    orthogonal to the toolchain — the same bitstream can often be loaded
    by either a vendor-bundled programmer or a third-party tool like
    `openFPGALoader`. See programmers.yml for the full schema."""
    items = _load_yaml(os.path.join(dir_path, "programmers.yml"), "Programmers")
    return _unique_catalog(items, os.path.join(dir_path, "programmers.yml"), "Programmer")


def read_producers():
    """config/producers.yml: who makes boards, chips and modules (Digilent,
    Trenz Electronic, Sipeed, and the chip vendors that ship their own dev
    kits). Boards, chip families and mezzanines name a producer by id.
    {id: producer}."""
    return _registry("producers.yml", "Producers", "producer")


def _registry(name, root, kind, key="id"):
    """{id: entry} of a single-file registry config/<name>, its entries under
    `root`, identified by `key`."""
    path = os.path.join(dir_path, name)
    return _unique_catalog(_load_yaml(path, root), path, kind, key=key)


def read_features():
    """config/features.yml: the browsable classes of hardware a board may list
    (user_leds, hdmi_output, sdr_sdram, ...), each with the capabilities a
    device of that class can provide. {id: feature}."""
    return _registry("features.yml", "Features", "feature")


def read_kinds():
    """config/kinds.yml: the kinds a board's banks give their devices (the
    inventory's taxonomy: leds, sdcard, flash, ...), each with the features a
    board with such a bank lists one of. {kind: entry}."""
    return _registry("kinds.yml", "Kinds", "kind")


def read_design_top():
    """config/design_top.yml: the sections of the virtual device — which
    capabilities put parameters and ports on design_top, in what order, under
    which headings ({"sections": [{"title", "capabilities"}]})."""
    return _load_yaml(os.path.join(dir_path, "design_top.yml"), "DesignTop")


def read_devices():
    """config/devices.yml: named chips and modules (a TLV320AIC23B codec, an
    RTL8211 PHY), each in one feature class, with the peripherals that drive
    it. {id: device}."""
    return _registry("devices.yml", "Devices", "device")


def _walk_mezzanine_catalog_files():
    """Yield (catalog_yml_path, producer_dir, family_basename) for every
    config/mezzanines/<producer>/<family>.yml file."""
    base = os.path.join(dir_path, "mezzanines")
    if not os.path.isdir(base):
        return
    for prod_name in sorted(os.listdir(base)):
        if prod_name.startswith("_"):
            continue
        prod_dir = os.path.join(base, prod_name)
        if not os.path.isdir(prod_dir):
            continue
        for fname in sorted(os.listdir(prod_dir)):
            if not fname.endswith(".yml"):
                continue
            fam_path = os.path.join(prod_dir, fname)
            if os.path.isfile(fam_path):
                yield fam_path, prod_name, fname[:-4]


def read_mezzanines():
    """Every config/mezzanines/<producer>/<family>.yml: SoMs, mezzanine cards,
    piggyback boards and carriers, catalogue only. {id: mezzanine} with _path,
    _producer_dir and _family_dir."""
    out = {}
    for fam_path, prod_name, fam_name in _walk_mezzanine_catalog_files():
        data = _read_yaml_file(fam_path) or {}
        for ordinal, entry in enumerate(data.get("Mezzanines") or [], 1):
            mid = entry.get("id") if isinstance(entry, dict) else None
            if not isinstance(mid, str) or not mid.strip():
                raise ConfigError("{}: mezzanine {} needs a nonempty string id".format(fam_path, ordinal))
            if mid in out:
                raise ConfigError("duplicate mezzanine id {!r}: {} and {}".format(mid, out[mid]["_path"], fam_path))
            entry["_path"] = fam_path
            entry["_producer_dir"] = prod_name
            entry["_family_dir"] = fam_name
            out[mid] = entry
    return out


# ---------------------------------------------------------------------------
# Boards: one file each, config/boards/<producer>/<family>/<id>.yml
# ---------------------------------------------------------------------------


def _walk_board_files():
    """Yield (path, producer_dir, family_dir, board_id) for every board file
    config/boards/<producer>/<family>/<id>.yml, skipping `_` directories."""
    base = os.path.join(dir_path, "boards")
    if not os.path.isdir(base):
        return
    for prod_name in sorted(os.listdir(base)):
        prod_dir = os.path.join(base, prod_name)
        if prod_name.startswith("_") or not os.path.isdir(prod_dir):
            continue
        for fam_name in sorted(os.listdir(prod_dir)):
            fam_dir = os.path.join(prod_dir, fam_name)
            if fam_name.startswith("_") or not os.path.isdir(fam_dir):
                continue
            for fname in sorted(os.listdir(fam_dir)):
                if fname.endswith(".yml") and not fname.startswith("_"):
                    yield os.path.join(fam_dir, fname), prod_name, fam_name, fname[:-4]


_BOARDS = {}
_BOARDS_FROZEN = [0]        # > 0: the index is not checked against the files (see boards_frozen)


@contextlib.contextmanager
def boards_frozen():
    """Within the block the board index is checked against the files once, on
    entry, not on every read: for loops over every rig (read_configurations,
    ./unifpga setup check) that would otherwise stat every board file per rig."""
    _boards_index()
    _BOARDS_FROZEN[0] += 1
    try:
        yield
    finally:
        _BOARDS_FROZEN[0] -= 1


def _boards_index():
    """Validated, cached {board id: the file's Board mapping plus `family`
    ({id, name, producer} of the chip family its directory names), _path,
    _producer_dir, _family_dir}; callers must not mutate its entries."""
    if _BOARDS_FROZEN[0] and _BOARDS.get("value") is not None:
        return _BOARDS["value"]
    files = list(_walk_board_files())
    key = tuple((p, os.stat(p).st_mtime_ns, os.stat(p).st_size) for p, _pr, _f, _b in files) + \
          tuple((p, os.stat(p).st_mtime_ns, os.stat(p).st_size) for p, _pr, _f in _walk_chip_registry_files())
    if _BOARDS.get("key") != key:                       # a board or a chip family changed
        families = _families_by_dir()
        out = {}
        for path, prod_name, fam_name, board_id in files:
            data = _parsed(path)
            board = data.get("Board") if isinstance(data, dict) else None
            if not isinstance(board, dict) or board.get("id") != board_id:
                raise ConfigError("{}: needs a Board mapping whose id is {!r}".format(path, board_id))
            if board_id in out:
                raise ConfigError("duplicate board {!r}: {} and {}".format(board_id, out[board_id]["_path"], path))
            family = families.get((prod_name, fam_name))
            if family is None:
                raise ConfigError("{}: no chip family config/chips/{}/{}.yml for the board's directory"
                                  .format(path, prod_name, fam_name))
            entry = dict(board)
            entry["family"] = family
            entry["_path"] = path
            entry["_producer_dir"] = prod_name
            entry["_family_dir"] = fam_name
            out[board_id] = entry
        _BOARDS.update(key=key, value=out)
    return _BOARDS["value"]


def read_boards():
    """{board id: board} — every board file (config/boards/<producer>/<family>/<id>.yml),
    a copy. This is the board: identity and catalogue fields, chip or chips,
    programmer, banks of pins, verification, as drawn its headers and parts,
    and `family` ({id, name, producer}: the chip family its directory names)."""
    return copy.deepcopy(_boards_index())


def read_board(board_id):
    """One board (a copy), or None."""
    board = _boards_index().get(board_id)
    return copy.deepcopy(board) if board is not None else None


def _families_by_dir():
    """{(producer_dir, family_dir): {id, name, producer}} from the chip family files."""
    out = {}
    for fam_path, prod_dir, fam_slug in _walk_chip_registry_files():
        data = _parsed(fam_path)
        fam = data.get("Family") if isinstance(data, dict) else None
        if isinstance(fam, dict):
            out[(prod_dir, fam_slug)] = {"id": fam_slug, "name": fam.get("name"), "producer": fam.get("producer")}
    return out


def peek_boards():
    """The boards as read_boards gives them, but the cached originals, not
    copies: for read-only loops over every board. Never mutate them."""
    return _boards_index()


def peek_board(board_id):
    """One board, the cached original (read-only), or None."""
    return _boards_index().get(board_id)


def board_chips(board):
    """[(chip id, variant name or None)]: the board's `chip`, or its `chips`
    (the variants a rig's `part:` chooses between)."""
    if board.get("chip"):
        return [(board["chip"], None)]
    out = []
    for entry in board.get("chips") or []:
        if isinstance(entry, dict):
            if entry.get("id"):
                out.append((entry["id"], entry.get("name")))
        elif entry:
            out.append((str(entry), None))
    return out


def _walk_chip_registry_files():
    """Yield (path, producer_dir, family_yml_basename) for every chip
    registry file under config/chips/<producer>/<family>.yml."""
    base = os.path.join(dir_path, "chips")
    if not os.path.isdir(base):
        return
    for prod_name in sorted(os.listdir(base)):
        if prod_name.startswith("_"):
            continue
        prod_dir = os.path.join(base, prod_name)
        if not os.path.isdir(prod_dir):
            continue
        for fname in sorted(os.listdir(prod_dir)):
            if not fname.endswith(".yml"):
                continue
            yield os.path.join(prod_dir, fname), prod_name, fname[:-4]


def read_chips():
    """Every chip of every family file config/chips/<producer>/<family>.yml:
    {chip id: {id, toolchains, producer, family, family_name, _path}} — the
    chip's own `toolchains` or the family's `default_toolchains`, the
    producer's id, the family's id (the file's name) and the family's name
    as the vendor writes it."""
    out = {}
    origins = {}
    for fam_path, _prod_dir, fam_slug in _walk_chip_registry_files():
        data = _read_yaml_file(fam_path)
        fam = data.get("Family") if isinstance(data, dict) else None
        if not isinstance(fam, dict):
            raise ConfigError("{}: needs a Family mapping".format(fam_path))
        if any(not isinstance(fam.get(field), str) or not fam[field].strip()
               for field in ("producer", "name")):
            raise ConfigError("{}: the family needs a producer and a name".format(fam_path))
        if fam.get("id") != fam_slug:
            raise ConfigError("{}: the family's id must be {!r}, the file's name".format(fam_path, fam_slug))
        default_tcs = fam.get("default_toolchains") or []
        chips = fam.get("chips")
        if not isinstance(chips, list):
            raise ConfigError("{}: chips must be a list".format(fam_path))
        for ordinal, chip in enumerate(chips, 1):
            if not isinstance(chip, dict) or not isinstance(chip.get("id"), str) or not chip["id"].strip():
                raise ConfigError("{}: chip item {} needs a nonempty string id".format(fam_path, ordinal))
            cid = chip["id"]
            if cid in out:
                raise ConfigError("duplicate chip id {!r}: {} and {}".format(cid, origins[cid], fam_path))
            entry = dict(chip)
            entry["toolchains"] = list(chip.get("toolchains") or default_tcs)
            entry["producer"] = fam["producer"]
            entry["family"] = fam_slug
            entry["family_name"] = fam["name"]
            entry["_path"] = fam_path
            out[cid] = entry
            origins[cid] = fam_path
    return out


def parse_versioned_ref(ref):
    """Parse a versioned reference like `vivado[2017.4+]`, `iceprog[*]`, or
    `mojoload`. Returns (id, version_constraint_string_or_None).

    Used for toolchain and programmer references in chip / board entries.
    The version constraint is returned verbatim — the caller decides how
    to interpret it (matching, ordering, etc.).
    """
    try:
        return _parse_versioned_ref(ref)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def programmers_for_board(board_id, *, boards=None, chips=None, programmers=None):
    """The programmers usable on a board, in resolution order (additive):

      1. **Bundled / chip-tied via toolchain:** a programmer whose `bundled`
         toolchain is among the toolchains of the board's chips.
      2. **Third-party with chip-family support:** a programmer whose
         `families` names the family of one of the board's chips and whose
         `requires_bridge`, if any, is among the board's `bridges`.
      3. **Bootloader-tied:** a programmer whose `requires_bootloader` is the
         board's `bootloader`.
      4. **Board-explicit:** every entry of the board's `extra_programmers`
         (parsed for version constraints).

    Returns the programmers.yml entries; the board's `programmer` is the
    explicit default a caller may prefer."""
    if boards is None: boards = _boards_index()
    if chips is None: chips = read_chips()
    if programmers is None: programmers = read_programmers()

    board = boards.get(board_id)
    if board is None:
        raise ConfigError("Unknown board: {b}".format(b=board_id))
    bridges = set(board.get("bridges") or [])
    bootloader = board.get("bootloader")

    chip_toolchain_ids = set()
    chip_families = set()
    for cid, _name in board_chips(board):
        chip = chips.get(cid)
        if chip is None:
            log.warning("Board %s references unknown chip %s", board_id, cid)
            continue
        chip_families.add(chip.get("family"))
        for ref in chip.get("toolchains", []):
            tc_id, _ = parse_versioned_ref(ref)
            chip_toolchain_ids.add(tc_id)

    result = []
    seen = set()
    def add(p):
        if p["id"] not in seen:
            result.append(p)
            seen.add(p["id"])

    for pid, p in programmers.items():                      # 1. bundled with a toolchain of the chips
        if p.get("bundled") and p["bundled"] in chip_toolchain_ids:
            req = p.get("requires_bridge")
            if req and req not in bridges:
                continue
            add(p)
    for pid, p in programmers.items():                      # 2. third-party, by chip family
        if p.get("bundled") or not chip_families & set(p.get("families") or []):
            continue
        req = p.get("requires_bridge")
        if req and req not in bridges:
            continue
        add(p)
    if bootloader:                                          # 3. by bootloader
        for pid, p in programmers.items():
            if p.get("requires_bootloader") == bootloader:
                add(p)
    for ref in board.get("extra_programmers") or []:         # 4. named by the board
        pid, _ = parse_versioned_ref(ref)
        if pid in programmers:
            add(programmers[pid])
        else:
            log.warning("Board %s references unknown programmer %s", board_id, pid)
    return result


def read_peripherals():
    """Read every peripheral contract under config/peripherals/."""
    return _load_yaml_dir("peripherals", "Peripheral", "id")


def read_capabilities():
    """Read every capability contract under config/capabilities/."""
    return _load_yaml_dir("capabilities", "Capability", "id")


def _read_configuration(configuration_id):
    """One rig's configuration, or None when there is no such rig."""
    return read_configurations().get(configuration_id)


_CONFIGURATIONS = {}          # conventions flag -> (source key, {id: configuration})


def _rig_sources_key(setup_dir):
    """What the rigs' configurations depend on: the setups (`setup_dir`: a
    test may stand in another directory), the boards (their banks and drawn
    headers and parts), the modules and the connectors, by path and
    modification time."""
    key = []
    for base in (setup_dir, os.path.join(dir_path, "modules")):
        for name in sorted(os.listdir(base)) if os.path.isdir(base) else ():
            path = os.path.join(base, name)
            key.append((path, os.stat(path).st_mtime_ns))
    for path, _prod, _fam, _board in _walk_board_files():
        key.append((path, os.stat(path).st_mtime_ns))
    path = os.path.join(dir_path, "connectors.yml")
    if os.path.exists(path):
        key.append((path, os.stat(path).st_mtime_ns))
    return tuple(key)


def read_configurations(conventions=None):
    """Every rig's configuration, {id: dict}: the expansion of its setup
    (config/setups/<id>.yml, tools/setup.py) with the board's layout. Nothing
    is read from a configurations directory: the rig is its setup. `conventions`
    False leaves the setup's design section and design_bits out (default: the
    UNIFPGA_PROFILE switch)."""
    from tools import setup as su                  # tools/setup.py imports this module
    if conventions is None:
        conventions = su.conventions_enabled()
    key = _rig_sources_key(su.SETUP_DIR)
    hit = _CONFIGURATIONS.get(conventions)
    if hit is not None and hit[0] == key:
        return copy.deepcopy(hit[1])
    setups = su.read_setups()
    out = {}
    with boards_frozen():                          # one look at the board files, not one per rig
        for sid in sorted(setups):
            try:
                out[sid] = su.generate(setups[sid], conventions)
            except su.SetupError as exc:
                raise ConfigError("Configuration '{}': {}".format(sid, exc))
    _CONFIGURATIONS[conventions] = (key, out)
    return copy.deepcopy(out)


# ---------------------------------------------------------------------------
# rigs and build targets
#
# A configuration is one rig. It builds with every toolchain in `toolchains:`
# (default `toolchain:`, first) and for every chip in `parts:` (default
# `part:`); `for_toolchain: {<toolchain>: <patch>}` (config/overlay.py), on the
# rig and on an attach, holds what a toolchain needs changed. `aliases: {<id>: {toolchain:, part:}}` keeps
# the ids of the per-toolchain and per-chip copies this used to be. A build
# target is (rig, toolchain, part); its id is the alias naming it, the rig id
# for the defaults, else <rig>@<toolchain>[@<part>].
# ---------------------------------------------------------------------------

def rig_toolchains(cfg):
    """The toolchains a rig is checked with, its default first."""
    return list(cfg.get("toolchains") or [cfg.get("toolchain")])


def rig_parts(cfg):
    """The chips a rig is checked with, its default first ([None]: the board has one)."""
    return list(cfg.get("parts") or [cfg.get("part")])


_ALIAS_INDEX = {}


def _alias_index():
    """{alias id: (rig id, toolchain or None, part or None)}, rebuilt when a
    setup file changes (the files are parsed once, not copied)."""
    base = os.path.join(dir_path, "setups")
    paths = [os.path.join(base, n) for n in sorted(os.listdir(base)) if n.endswith(".yml") and not n.startswith("_")]
    key = tuple((p, os.stat(p).st_mtime_ns, os.stat(p).st_size) for p in paths)
    if _ALIAS_INDEX.get("key") != key:
        out = {}
        for p in paths:
            cfg = (_parsed(p) or {}).get("Setup") or {}
            for alias, target in (cfg.get("aliases") or {}).items():
                target = target or {}
                out[alias] = (cfg.get("id"), target.get("toolchain"), target.get("part"))
        _ALIAS_INDEX.update(key=key, value=out)
    return _ALIAS_INDEX["value"]


_PLAIN_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")


def target_of(ident):
    """(rig id, toolchain, part) a build-target id names, None for the rig's
    defaults; None when it names nothing."""
    ident = str(ident)
    if "@" in ident:
        rig_id, _, rest = ident.partition("@")
        toolchain, _, part = rest.partition("@")
        # the id names a run directory: only a known toolchain and a plain chip name
        if not toolchain or toolchain not in read_toolchains() or (part and not _PLAIN_PART.match(part)):
            return None
        if _read_configuration(rig_id) is None:
            return None
        return rig_id, toolchain, part or None
    alias = _alias_index().get(ident)          # an alias is never a rig's id (tools/setup.py validate)
    if alias is not None:
        return alias
    if _read_configuration(ident) is not None:
        return ident, None, None
    return None


def target_id(rig_id, cfg, toolchain, part):
    """The id of build target (rig, toolchain, part)."""
    toolchain = toolchain or cfg.get("toolchain")
    part = part if part is not None else cfg.get("part")
    if toolchain == cfg.get("toolchain") and part == cfg.get("part"):
        return rig_id
    for alias, t in (cfg.get("aliases") or {}).items():
        t = t or {}
        if (t.get("toolchain") or cfg.get("toolchain")) == toolchain and \
                (t.get("part") if t.get("part") is not None else cfg.get("part")) == part:
            return alias
    return "@".join([rig_id, toolchain] + ([str(part)] if part is not None and part != cfg.get("part") else []))


def build_targets(configurations=None):
    """[{id, rig, toolchain, part}] for every rig x checked toolchain x checked part."""
    out = []
    for rig_id, cfg in sorted((configurations or read_configurations()).items()):
        for toolchain in rig_toolchains(cfg):
            for part in rig_parts(cfg):
                out.append({"id": target_id(rig_id, cfg, toolchain, part), "rig": rig_id,
                            "toolchain": toolchain, "part": part})
    return out


def for_target(cfg, toolchain=None, part=None):
    """The configuration as `toolchain` / `part` build it: its `for_toolchain`
    patch applied, `toolchain:` and `part:` set."""
    from config import overlay
    toolchain = toolchain or cfg.get("toolchain")
    cfg = overlay.select(cfg, toolchain, "attach")
    cfg["toolchain"] = toolchain
    if part is not None:
        cfg["part"] = part
    return cfg


def toolchain_constraints(boards, chips, board_id, toolchain_id, selected_chip_id=None):
    """Chip constraints for this board/toolchain pair, across chip variants.

    An empty list means the toolchain is not catalogued for this board. A
    matching unqualified reference is represented by the wildcard '*'.
    """
    if board_id not in boards:
        raise ConfigError("Board {b} was not found in the catalog".format(b=board_id))
    board = boards[board_id]
    chip_ids = [cid for cid, _name in board_chips(board)]
    if not chip_ids:
        raise ConfigError("Board {b} has no chip / chips field — can't determine toolchain support".format(b=board_id))
    if selected_chip_id is not None:
        if selected_chip_id not in chip_ids:
            raise ConfigError("Chip '{c}' is not a variant of board '{b}'"
                              .format(c=selected_chip_id, b=board_id))
        chip_ids = [selected_chip_id]

    constraints = []
    for cid in chip_ids:
        chip = chips.get(cid)
        if chip is None:
            log.warning("Board %s references unknown chip %s", board_id, cid)
            continue
        for ref in chip.get("toolchains", []):
            tc_id, constraint = parse_versioned_ref(ref)
            if tc_id == toolchain_id:
                constraints.append(constraint or "*")
    return constraints


def is_compatible(boards, chips, board_id, toolchain_id):
    """Whether the board lists this toolchain, before version admission."""
    return bool(toolchain_constraints(boards, chips, board_id, toolchain_id))


def require_toolchain_version(toolchain):
    """Reject an installation that conflicts with its pin or chip limits."""
    configured = toolchain.get("configured_version")
    detected = toolchain.get("detected_install_version")
    if toolchain.get("detect_source") and configured:
        if not detected:
            raise ConfigError("Toolchain '{t}' version could not be determined for "
                              "the detected installation; configured version is {v}"
                              .format(t=toolchain["id"], v=configured))
        if str(configured).casefold() != str(detected).casefold():
            raise ConfigError("Toolchain '{t}' detected version {found} conflicts "
                              "with configured version {expected}"
                              .format(t=toolchain["id"], found=detected,
                                      expected=configured))
    version = detected if toolchain.get("detect_source") else configured
    constraints = toolchain.get("chip_version_constraints") or []
    if not constraints:
        raise ConfigError("Toolchain '{t}' has no chip compatibility evidence"
                          .format(t=toolchain["id"]))
    from tools import toolchain_detect
    try:
        matches = [toolchain_detect.matches_version(version, constraint)
                   for constraint in constraints]
        if any(matches):
            return
    except ValueError as exc:
        raise ConfigError("Toolchain '{t}' has invalid chip version constraint: {e}"
                          .format(t=toolchain["id"], e=exc)) from exc
    raise ConfigError("Toolchain '{t}' version {v} does not meet chip constraint(s) {c}"
                      .format(t=toolchain["id"], v=version or "unknown",
                              c=", ".join(constraints)))


def resolve_toolchain_install(toolchain):
    """Copy of a toolchains.yml entry with the install resolved by
    tools/toolchain_detect.py: the `install_dir` pin when it exists, else the
    vendor environment variable, PATH, then the default install parents.
    Adds `bin_dirs` (for PATH), `bins`, `detect_source`, `detect_notes`, and
    separate configured and install-path version evidence
    (`configured_version`, `detected_install_version`); leaves `install_dir`
    as written when nothing is found so the driver's own error message still
    names it."""
    tc = dict(toolchain)
    tc["configured_version"] = str(tc["version"]) if tc.get("version") else None
    try:
        from tools import toolchain_detect
    except ImportError:              # config/ imported without the repo root on sys.path
        return tc
    det = toolchain_detect.detect(tc["id"], pin=tc.get("install_dir"))
    tc["detect_source"] = det.source
    tc["detected_install_version"] = str(det.version) if det.found and det.version else None
    tc["detect_notes"] = list(det.notes)
    tc["bin_dirs"] = list(det.bin_dirs)
    tc["bins"] = dict(det.bins)
    if det.found:
        if det.install_dir:
            tc["install_dir"] = det.install_dir
        if det.version and not tc.get("version"):
            tc["version"] = det.version
    return tc


def resolve_configuration(configuration_id, configuration=None, toolchain=None, part=None):
    """
    Look up a configuration by id and return a fully-resolved bundle:

        {
          "configuration": <Configuration dict>,
          "board":         <the board (config.init.read_board) with the chip selected —
                            chip_id, part (as the toolchain writes it), part_name — and the
                            configuration's pin / io overrides applied to its banks>,
          "toolchain":     <Toolchain dict from toolchains.yml>,
          "peripherals": [
              {"peripheral_id": ..., "peripheral": <Peripheral dict>,
               "params": {...}, "bind": {...}},
              ...
          ],
        }

    `configuration_id` is a rig or a build-target id (an alias, or
    <rig>@<toolchain>[@<part>]); `toolchain` / `part` choose another build of
    the rig. The bundle's "target" is {id, rig, toolchain, part}.

    `configuration`: resolve this Configuration dict instead of the file of
    that id (an unsaved setup in the board editor).

    Raises ConfigError on any inconsistency.
    """
    rig_id = configuration_id
    if configuration is not None:
        cfg = copy.deepcopy(configuration)
    else:
        target = target_of(configuration_id)
        if target is None:
            raise ConfigError("Unknown configuration '{c}'. Run init_settings.py to pick one."
                              .format(c=configuration_id))
        rig_id = target[0]
        toolchain = toolchain or target[1]
        part = part if part is not None else target[2]
        cfg = _read_configuration(rig_id)
    rig_cfg = cfg
    cfg = for_target(cfg, toolchain, part)

    boards = _boards_index()                # read-only; the board is copied below
    toolchains = read_toolchains()
    chips = read_chips()
    peripherals = read_peripherals()

    board_id = cfg.get("board")
    toolchain_id = cfg.get("toolchain")
    if not board_id or not toolchain_id:
        raise ConfigError("Configuration '{c}' is missing board: or toolchain:".format(c=configuration_id))
    if board_id not in boards:
        raise ConfigError("Configuration '{c}' references unknown board '{b}'"
                          .format(c=configuration_id, b=board_id))
    if toolchain_id not in toolchains:
        raise ConfigError("Configuration '{c}' references unknown toolchain '{t}'"
                          .format(c=configuration_id, t=toolchain_id))

    # The chip the build targets: the board's chip, or the variant the
    # configuration's `part:` names (Arty A7 35T / 100T, Nexys A7 50T / 100T,
    # OrangeCrab 25F / 85F); `part` is that chip as the toolchain writes it.
    board = copy.deepcopy(boards[board_id])
    variants = board_chips(board)
    if not variants:
        raise ConfigError("Board '{b}' has no chip / chips field".format(b=board_id))
    for cid, _name in variants:
        if cid not in chips:
            raise ConfigError("Board '{b}' references unknown chip '{c}'".format(b=board_id, c=cid))
    wanted = cfg.get("part")
    if wanted is not None:
        w = str(wanted).strip().lower()
        chosen = next(((cid, name) for cid, name in variants if w in {str(name or "").lower(), cid.lower()}), None)
        if chosen is None:
            raise ConfigError(
                "Configuration '{c}': part: {w!r} is not one of the board's chips ({opts})"
                .format(c=configuration_id, w=wanted,
                        opts=", ".join("{}={}".format(name or "?", cid) for cid, name in variants)))
    else:
        if len(variants) > 1:
            log.warning("Configuration '%s': board '%s' has %d chips but no part: is set; "
                        "toolchains will default to %s (audit code PART)",
                        configuration_id, board_id, len(variants), variants[0][0])
        chosen = variants[0]
    selected_chip_id, part_name = chosen
    board["chip_id"] = selected_chip_id
    board["part_name"] = part_name
    board["part"] = _tool_part(selected_chip_id, toolchain_id)

    constraints = toolchain_constraints(boards, chips, board_id, toolchain_id,
                                        selected_chip_id=selected_chip_id)
    if not constraints:
        raise ConfigError("Configuration '{c}': toolchain '{t}' is not compatible with "
                          "selected chip '{chip}' on board '{b}'"
                          .format(c=configuration_id, t=toolchain_id,
                                  chip=selected_chip_id, b=board_id))
    resolved_toolchain = resolve_toolchain_install(toolchains[toolchain_id])
    resolved_toolchain["chip_version_constraints"] = constraints

    if not board.get("banks"):
        raise ConfigError("Board '{b}' has no banks of pins in config/boards/{p}/{f}/{b}.yml — a catalogue-only board"
                          .format(b=board_id, p=board["_producer_dir"], f=board["_family_dir"]))
    _apply_pin_overrides(configuration_id, cfg, board)
    _apply_io_overrides(configuration_id, cfg, board)

    attached = []
    for attach_index, entry in enumerate(cfg.get("attach", []) or []):
        perip_id = entry.get("peripheral")
        if perip_id is None:
            raise ConfigError("Configuration '{c}': an attach entry has no peripheral"
                              .format(c=configuration_id))
        if perip_id not in peripherals:
            raise ConfigError("Configuration '{c}': unknown peripheral '{p}'"
                              .format(c=configuration_id, p=perip_id))
        attached.append({
            "peripheral_id": perip_id,
            "peripheral":    peripherals[perip_id],
            "params":        entry.get("params", {}) or {},
            "bind":          entry.get("bind", {}) or {},
            # `design_bits: {leds: [0, 1, ...]}` — which bits of the design's
            # bus this provider occupies (a TM1638 can share the design's led /
            # key buses with the board's own LEDs and keys instead of extending
            # them); absent = the next free bits, in attach order
            "design_bits":   entry.get("design_bits", {}) or {},
            # position in the configuration's attach list (tools/trace.py
            # relates providers back to it)
            "attach_index":  attach_index,
        })

    # (how the design sees the rig — its reset, design clock, bus widths, ties
    # and each part's design bits — is in the configuration already: the
    # setup's design section, tools/setup.py)

    # `tie:` — pins the top drives with a constant or the reset (e.g.
    # `assign M_CLK = 1'b0`, `assign ARDUINO_RESET_N = ~ rst`), one pin_tie
    # attach each so they are declared, driven and constrained.
    tie = next((pid for pid, p in peripherals.items() if p.get("role") == "tie"), None)
    for ref, value in (cfg.get("tie") or {}).items():
        if tie is None:
            raise ConfigError("Configuration '{c}': tie: needs a peripheral with role: tie".format(c=configuration_id))
        attached.append({
            "peripheral_id": tie,
            "peripheral":    peripherals[tie],
            "params":        {"value": _tie_value(configuration_id, ref, value)},
            "bind":          {"pin": str(ref)},
        })

    return {
        "configuration": cfg,
        "board":         board,
        "toolchain":     resolved_toolchain,
        "peripherals":   attached,
        "target":        {"id": target_id(rig_id, rig_cfg, toolchain_id, cfg.get("part")) if configuration is None
                          else configuration_id,
                          "rig": rig_id, "toolchain": toolchain_id, "part": cfg.get("part")},
    }


_TIE_VALUES = {
    "0": "const.0", "1": "const.1", "false": "const.0", "true": "const.1",
    "rst": "context.rst", "~rst": "~context.rst", "rst_n": "~context.rst", "!rst": "~context.rst",
    "const.0": "const.0", "const.1": "const.1", "context.rst": "context.rst", "~context.rst": "~context.rst",
}


def _tie_value(configuration_id, ref, value):
    """`tie:` value -> codegen reference (`const.0`, `~context.rst`, ...)."""
    key = str(value).strip().replace(" ", "").lower()
    if key not in _TIE_VALUES:
        raise ConfigError("Configuration '{c}': tie {r!r}: value {v!r} is not one of 0, 1, rst, ~rst"
                          .format(c=configuration_id, r=ref, v=value))
    return _TIE_VALUES[key]


def _apply_pin_overrides(configuration_id, cfg, board):
    """Apply the configuration's `pin_overrides:` to its private copy of the
    board's banks. A variant that wires a header differently from the board's
    default (`tang_nano_20k_lcd_800_480_49mhz_tm1638` uses another LCD
    adapter) says so here instead of getting a second board:

        pin_overrides:
          onboard_lcd.r:  ["42", "41", "49", "39", "38"]   # replace a sub-key
          onboard_lcd.bl: null                             # remove a sub-key
          pmod_x:         { pins: [..], frequency_mhz: 50 } # replace a whole bank
    """
    overrides = cfg.get("pin_overrides") or {}
    if not overrides:
        return
    banks = board.setdefault("banks", {})
    for ref, value in overrides.items():
        parts = str(ref).split(".")
        bank_name = parts[0]
        sub = parts[1] if len(parts) > 1 else None
        if len(parts) > 2:
            raise ConfigError("Configuration '{c}': pin_overrides key {r!r} has more than one dot"
                              .format(c=configuration_id, r=ref))
        if sub is None:
            if value is None:
                banks.pop(bank_name, None)
            elif isinstance(value, dict) and "pins" in value:
                banks[bank_name] = value
            else:
                banks.setdefault(bank_name, {})["pins"] = value
            continue
        bank = banks.setdefault(bank_name, {"pins": {}})
        pins = bank.get("pins")
        if not isinstance(pins, dict):
            raise ConfigError("Configuration '{c}': pin_overrides {r!r} names a sub-key but bank "
                              "{b!r} is not a sub-keyed bank".format(c=configuration_id, r=ref, b=bank_name))
        if value is None:
            pins.pop(sub, None)
        else:
            pins[sub] = value


_ORDERING_CODE = re.compile(r"^(XC[67][A-Z0-9]*?)-(\d)([A-Z]{2,3}\d+)([CIQ]?)$", re.I)


def _tool_part(part, toolchain_id):
    """A chip registry Id that is a Xilinx ordering code (`XC7A35T-2FGG484I`,
    `XC6SLX9-2TQG144C`) rendered the way the tool wants the part:
    Vivado / nextpnr-xilinx `xc7a35tfgg484-2`, ISE `xc6slx9-2-tqg144`.
    Anything else passes through."""
    m = _ORDERING_CODE.match(str(part))
    if not m:
        return part
    dev, speed, pkg = m.group(1).lower(), m.group(2), m.group(3).lower()
    if toolchain_id == "ise":
        return "{}-{}-{}".format(dev, speed, pkg)
    return "{}{}-{}".format(dev, pkg, speed)


def _apply_io_overrides(configuration_id, cfg, board):
    """Apply the configuration's `io_overrides:` (IO standard per bank, sub-key
    or pin) to its private copy of the board's banks. The Gowin variants type pins per
    variant (the Tang Nano 9K HDMI variants put LVCMOS33 on CLK, the LCD
    variants type nothing), so the board keeps what every variant agrees on
    and each configuration carries its own additions:

        io_overrides:
          clk:            LVCMOS33      # whole bank
          onboard_lcd.r:  LVCMOS33      # one sub-key
          "gpio[0]":      LVCMOS33      # one pin
    """
    overrides = cfg.get("io_overrides") or {}
    if not overrides:
        return
    banks = board.setdefault("banks", {})
    for ref, value in overrides.items():
        m = re.match(r"^([A-Za-z_]\w*)(?:\.(\w+))?(?:\[(\d+)\])?$", str(ref).strip())
        if not m:
            raise ConfigError("Configuration '{c}': io_overrides key {r!r} is not bank / bank.sub / bank[i]"
                              .format(c=configuration_id, r=ref))
        bank_name, sub, idx = m.group(1), m.group(2), m.group(3)
        bank = banks.get(bank_name)
        if bank is None:
            raise ConfigError("Configuration '{c}': io_overrides names unknown bank {b!r}"
                              .format(c=configuration_id, b=bank_name))
        if sub is None and idx is None:
            bank["iostandard"] = value
            continue
        pins = bank.get("pins")
        if sub is not None:
            if not isinstance(pins, dict) or sub not in pins:
                raise ConfigError("Configuration '{c}': io_overrides {r!r}: bank {b!r} has no sub-key {s!r}"
                                  .format(c=configuration_id, r=ref, b=bank_name, s=sub))
            pins = pins[sub]
        targets = pins if isinstance(pins, list) else [pins]
        if idx is not None:
            i = int(idx)
            if i >= len(targets) or targets[i] is None:
                raise ConfigError("Configuration '{c}': io_overrides {r!r}: no pin at index {i}"
                                  .format(c=configuration_id, r=ref, i=i))
            targets = [targets[i]]
        # a pin often sits in two banks (Tang Nano 9K: LCD colours on the TMDS
        # pairs); the type belongs to the pin, whichever bank the generated
        # port comes from
        for p in targets:
            if p is None:
                continue
            for other in banks.values():
                if _bank_has_pin(other, p):
                    other.setdefault("overrides", {})[str(p)] = value


def _bank_has_pin(bank, pin):
    pins = (bank or {}).get("pins")
    vals = []
    if isinstance(pins, dict):
        for v in pins.values():
            vals.extend(v if isinstance(v, list) else [v])
    elif isinstance(pins, list):
        vals = pins
    else:
        vals = [pins]
    p = str(pin).split(",", 1)[0].strip()
    return any(v is not None and str(v).split(",", 1)[0].strip() == p for v in vals)

def read_all(configuration_id=None, toolchain=None, part=None):
    """
    Read settings.yml + every other config file. Returns the fully-resolved
    configuration bundle, or None if no default has been set yet.
    """
    if configuration_id is None:
        settings_path = os.path.join(dir_path, "..", "settings.yml")
        if not os.path.exists(settings_path):
            return None
        try:
            with open(settings_path) as stream:
                settings = yaml.safe_load(stream)
        except yaml.YAMLError as exc:
            raise ConfigError("YAML parse error in settings.yml: {e}".format(e=exc))
        if not settings:
            return None
        configuration_id = settings.get("ConfigurationId")
        if configuration_id is None and "BoardId" in settings:
            raise ConfigError(
                "settings.yml uses the legacy {BoardId, Toolchain} format. "
                "Replace it with {ConfigurationId: <id>} or run init_settings.py "
                "to pick a configuration interactively."
            )

    if configuration_id is None:
        return None

    return resolve_configuration(configuration_id, toolchain=toolchain, part=part)


def _prompt_choice(prompt, count):
    raw = input(prompt)
    try:
        n = int(raw)
    except ValueError:
        raise ConfigError("Expected a number, got: {v!r}".format(v=raw))
    if not 1 <= n <= count:
        raise ConfigError("Number out of range (1..{c}): {n}".format(c=count, n=n))
    return n


def init():
    """
    Interactively pick a configuration in two stages: first a board, then a
    variant configuration on that board. Persist the choice to settings.yml
    and return the fully-resolved bundle.
    """
    rigs = read_configurations()
    if not rigs:
        raise ConfigError("No rigs found in config/setups/")
    boards = _boards_index()

    # Group the build targets (every rig with each toolchain / chip it is checked with) by board.
    by_board = {}
    for t in build_targets(rigs):
        cfg = for_target(rigs[t["rig"]], t["toolchain"], t["part"])
        by_board.setdefault(cfg.get("board"), []).append((t["id"], cfg))

    # Stage 1: pick a board.
    board_ids_with_configs = sorted(b for b in by_board if b in boards)
    print("\nAvailable boards (with configurations):\n")
    for i, bid in enumerate(board_ids_with_configs, start=1):
        b = boards[bid]
        print("  {i:3d}) {name}  ({producer} / {family}) — {n} configuration(s)".format(
            i=i,
            name=b.get("name", bid),
            producer=b["family"]["producer"],
            family=b["family"]["name"],
            n=len(by_board[bid]),
        ))
    n = _prompt_choice("\nEnter a board number: ", len(board_ids_with_configs))
    board_id = board_ids_with_configs[n - 1]

    # Stage 2: pick a configuration for that board.
    variants = sorted(by_board[board_id])
    if len(variants) == 1:
        cfg_id = variants[0][0]
        print("Only one configuration for this board: {}".format(cfg_id))
    else:
        print("\nConfigurations for {}:\n".format(boards[board_id].get("name", board_id)))
        for i, (cfg_id, cfg) in enumerate(variants, start=1):
            tc = cfg.get("toolchain", "?")
            desc = cfg.get("description", "").strip()
            print("  {i:3d}) {id}  [toolchain: {tc}]".format(i=i, id=cfg_id, tc=tc))
            if desc:
                print("       {}".format(desc))
        n = _prompt_choice("\nEnter a configuration number: ", len(variants))
        cfg_id = variants[n - 1][0]

    # Validate by resolving once before saving.
    resolved = resolve_configuration(cfg_id)

    settings_path = os.path.join(dir_path, "..", "settings.yml")
    with open(settings_path, "w") as f:
        yaml.safe_dump({"ConfigurationId": cfg_id}, f)

    return resolved


def read_or_init(configuration_id=None, toolchain=None, part=None):
    """
    Resolve a configuration: from the argument, settings.yml, or interactive prompt.
    """
    settings = read_all(configuration_id, toolchain=toolchain, part=part)
    if settings is not None:
        return settings
    return init()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        init()
    except ConfigError as exc:
        log.error("%s", exc)
        sys.exit(1)

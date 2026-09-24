#!/usr/bin/env python3
"""
Configuration loader.

A *configuration* (config/configurations/<id>.yml) is the unit of selection.
Each configuration declares a board + toolchain + a list of attached
peripherals. Picking a configuration resolves to a fully-loaded object that
synthesize.py and toolchain modules consume.
"""

import copy
import hashlib
import json
import os
import re
import sys
import logging

import yaml


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
# callers that annotate the dicts (read_boards_catalog injects `_catalog_path`
# etc.) never leak state into each other.
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


def _unique_catalog(items, source, kind):
    """Index a single-file registry without losing malformed or repeated IDs."""
    if not isinstance(items, list):
        raise ConfigError("{}: {} must be a list".format(source, kind))
    out = {}
    for ordinal, item in enumerate(items, 1):
        if (not isinstance(item, dict) or not isinstance(item.get("Id"), str) or
                not item["Id"].strip()):
            raise ConfigError("{}: {} item {} needs a nonempty string Id".format(
                source, kind, ordinal))
        if item["Id"] in out:
            raise ConfigError("{}: duplicate {} Id {!r}".format(source, kind, item["Id"]))
        out[item["Id"]] = item
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
    operations = toolchain.get("SupportedOperations")
    if not isinstance(operations, list) or any(
            not isinstance(op, str) or op not in ("synthesize", "program")
            for op in operations) or len(operations) != len(set(operations)):
        raise ConfigError("Toolchain '{t}' needs a unique SupportedOperations list "
                          "containing only synthesize and/or program"
                          .format(t=toolchain.get("Id", "?")))
    return operations


def require_toolchain_operation(toolchain, operation):
    """Reject a catalogue-only driver before a build or board action starts."""
    if operation not in supported_operations(toolchain):
        raise ConfigError("Toolchain '{t}' does not implement {op}; choose a "
                          "supported toolchain or implement its driver"
                          .format(t=toolchain.get("Id", "?"), op=operation))


def pinmap_fingerprint(pinmap):
    """Fingerprint the exact resolved board data used to generate constraints.

    Configuration pin and I/O overrides change this digest, so an attestation
    of the base board cannot accidentally authorize a different rig pinout.
    """
    try:
        encoded = json.dumps({k: v for k, v in pinmap.items() if k != "verification"},
                             sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ConfigError("Board pinmap cannot be fingerprinted: {}".format(exc))
    return hashlib.sha256(encoded).hexdigest()


def require_hardware_readiness(board, pinmap):
    """Admit physical builds only with review of the exact resolved pinmap.

    The YAML attestation is a review record, not a claim that this process can
    independently verify a vendor schematic. Missing records fail closed.
    """
    board_id = board.get("Id", "?")
    if not isinstance(pinmap, dict) or pinmap.get("id") != board_id:
        raise ConfigError("Board '{}' pinmap identity does not match".format(board_id))
    verification = pinmap.get("verification")
    if not isinstance(verification, dict) or verification.get("status") != "verified":
        raise ConfigError("Board '{}' pinmap is not verified for hardware; "
                          "use UNIFPGA_DRY_RUN=1 to inspect generated files"
                          .format(board_id))
    digest = verification.get("pinmap_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or \
            digest != pinmap_fingerprint(pinmap):
        raise ConfigError("Board '{}' pinmap verification digest is missing or stale"
                          .format(board_id))
    selected_part = board.get("PartOrderingCode") or board.get("Part")
    parts = verification.get("parts")
    if not isinstance(parts, list) or not parts or any(not isinstance(p, str) or not p for p in parts) \
            or len(parts) != len(set(parts)) or selected_part not in parts:
        raise ConfigError("Board '{}' pinmap verification does not cover selected part '{}'"
                          .format(board_id, selected_part))
    for subject in ("pinout", "electrical"):
        evidence = verification.get(subject)
        if not isinstance(evidence, dict) or any(
                not isinstance(evidence.get(field), str) or not evidence[field].strip()
                for field in ("source", "revision")):
            raise ConfigError("Board '{}' pinmap verification lacks {} source and revision"
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


def read_board_producers():
    """Read the registry of board producers from board_producers.yml.

    A board producer is the manufacturer / maker of a physical dev board
    (Digilent, Trenz Electronic, Sipeed, …). It is orthogonal to the
    chip producer (the silicon vendor — AMD/Xilinx, Intel/Altera, etc.).
    Each board's `BoardProducer:` field references an `Id:` from this
    registry; `read_board_producers_name_index()` builds a lookup that
    accepts the canonical Name plus any AKA aliases for migration / legacy
    references."""
    items = _load_yaml(os.path.join(dir_path, "board_producers.yml"), "Producers")
    return _unique_catalog(items, os.path.join(dir_path, "board_producers.yml"), "Producer")


def read_board_producers_name_index():
    """Map every known display-name / AKA string to its registry Id.

    Used during the BoardProducer-string-to-Id migration to resolve
    freeform display strings (e.g. "Xilinx (AMD)", "QMtech", "1BitSquared")
    back to canonical slug ids. Keys are case-sensitive — call sites
    should normalize as needed."""
    producers = read_board_producers()
    idx = {}
    for pid, p in producers.items():
        idx[p.get("Name", pid)] = pid
        idx[pid] = pid
        for aka in (p.get("AKA") or []):
            idx[aka] = pid
    return idx


def validate_board_producers(catalog=None, producers=None):
    """Verify every board's `BoardProducer:` field references a known
    producer Id (or, transitionally, a Name / AKA that resolves to one).

    Returns the list of unresolved BoardProducer references — empty list
    means clean. Useful as a CI gate after editing board catalogs."""
    if catalog is None:
        catalog = read_boards_catalog()
    if producers is None:
        producers = read_board_producers()
    idx = read_board_producers_name_index()
    unresolved = []
    for bid, b in catalog.items():
        bp = b.get("BoardProducer")
        if bp is None:
            continue
        if bp not in idx:
            unresolved.append((bid, bp))
    return unresolved


def read_features():
    """Read the abstract feature-family registry from features.yml.

    A feature is an abstract family of hardware (e.g. "audio_codec",
    "ethernet_phy_gigabit", "seven_segment_display") that a board may
    declare. Each feature optionally maps to one or more Capabilities
    (config/capabilities/*.yml) that a device of that family could
    provide to a design.

    Returns {feature_id: feature_info}."""
    items = _load_yaml(os.path.join(dir_path, "features.yml"), "Features")
    return _unique_catalog(items, os.path.join(dir_path, "features.yml"), "Feature")


# Back-compat alias for callers still using the old name.
def read_board_features():
    return read_features()


def read_peripheral_devices():
    """Read the specific peripheral devices registry from peripheral_devices.yml.

    A device is a specific physical chip/module (e.g. "TI TLV320AIC23B"
    audio codec, "Realtek RTL8211FD" Ethernet PHY). Each device tags itself
    with one Feature (the abstract family it belongs to) and optionally
    links to one or more PeripheralDrivers in config/peripherals/.

    Returns {device_id: device_info}."""
    items = _load_yaml(os.path.join(dir_path, "peripheral_devices.yml"), "Devices")
    return _unique_catalog(items, os.path.join(dir_path, "peripheral_devices.yml"), "Device")


def validate_board_features(catalog=None, features=None):
    """Warn when a board's `Features:` references an unknown feature Id.

    Returns a list of (board_id, unknown_feature) tuples. Empty list
    means clean. Features are optional on boards; this validator only
    flags tokens that aren't registered in features.yml."""
    if catalog is None:
        catalog = read_boards_catalog()
    if features is None:
        features = read_features()
    unknown = []
    for bid, b in catalog.items():
        for tok in (b.get("Features") or []):
            if tok not in features:
                unknown.append((bid, tok))
    return unknown


def validate_peripheral_devices(devices=None, features=None, peripherals=None):
    """Validate every device entry has a valid Feature reference and that
    each PeripheralDrivers entry resolves.

    Returns a dict with two keys:
      - "unknown_features": [(device_id, feature_ref), ...]
      - "unknown_peripherals": [(device_id, peripheral_ref), ...]
    """
    if devices is None:
        devices = read_peripheral_devices()
    if features is None:
        features = read_features()
    if peripherals is None:
        peripherals = read_peripherals()
    bad_feat = []
    bad_perif = []
    for did, d in devices.items():
        f = d.get("Feature")
        if f and f not in features:
            bad_feat.append((did, f))
        for pref in (d.get("PeripheralDrivers") or []):
            if pref not in peripherals:
                bad_perif.append((did, pref))
    return {"unknown_features": bad_feat, "unknown_peripherals": bad_perif}


def validate_board_devices(catalog=None, devices=None):
    """Verify every Devices entry on every board resolves to a known device Id.

    Returns a list of (board_id, unknown_device_id) tuples; empty list
    means clean. Devices are optional on boards (population is a slow,
    research-driven process); this validator only flags entries that
    reference unknown ids."""
    if catalog is None:
        catalog = read_boards_catalog()
    if devices is None:
        devices = read_peripheral_devices()
    unresolved = []
    for bid, b in catalog.items():
        for ref in (b.get("Devices") or []):
            # Devices entries can be plain strings or dicts with {Id, ...}
            if isinstance(ref, dict):
                ref_id = ref.get("Id")
            else:
                ref_id = ref
            if ref_id and ref_id not in devices:
                unresolved.append((bid, ref_id))
    return unresolved


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


def read_mezzanines_catalog():
    """Read every config/mezzanines/<producer>/<family>.yml — the registry
    for mezzanine cards, SoMs, and piggyback boards. Returns {id: entry}.

    Each entry has at minimum: Id, Name, Producer, Type
    (one of: mezzanine | som | piggyback | carrier), Connector (slug describing the
    physical interface to a host board). SoMs additionally have a Chip
    (the FPGA part on the module). Mezzanines have no Chip but list
    Devices (the peripheral chips populating the card).
    """
    catalog = {}
    for fam_path, prod_name, fam_name in _walk_mezzanine_catalog_files():
        data = _read_yaml_file(fam_path) or {}
        for entry in (data.get("Mezzanines") or []):
            entry["_registry_path"] = fam_path
            entry["_producer_dir"]  = prod_name
            entry["_family_dir"]    = fam_name
            mid = entry.get("Id")
            if not mid:
                raise ConfigError("Mezzanine in {p} missing Id".format(p=fam_path))
            if mid in catalog:
                raise ConfigError(
                    "Duplicate mezzanine Id {i!r} (in {a} and {b})".format(
                        i=mid, a=catalog[mid]["_registry_path"], b=fam_path))
            catalog[mid] = entry
    return catalog


def validate_mezzanines(catalog=None, devices=None, features=None,
                        chips=None, board_catalog=None, producers=None):
    """Check every mezzanine entry resolves cleanly.

    Returns a dict:
      unknown_devices:    [(mid, dev_id), ...]
      unknown_features:   [(mid, feat_id), ...]
      unknown_chips:      [(mid, chip_id), ...]   (SoMs only)
      unknown_producers:  [(mid, prod_slug), ...]
      unknown_compatible: [(mid, board_id), ...]
      missing_required:   [(mid, field), ...]
      bad_type:           [(mid, type), ...]
    """
    if catalog        is None: catalog        = read_mezzanines_catalog()
    if devices        is None: devices        = read_peripheral_devices()
    if features       is None: features       = read_features()
    if chips          is None: chips          = read_chips()
    if board_catalog  is None: board_catalog  = read_boards_catalog()
    if producers      is None: producers      = read_board_producers()

    # carrier: a board a SoM plugs into (Tang Primer 20K Dock, Enclustra base boards)
    valid_types = {"mezzanine", "som", "piggyback", "carrier"}
    out = {k: [] for k in ("unknown_devices", "unknown_features",
                            "unknown_chips", "unknown_producers",
                            "unknown_compatible", "missing_required",
                            "bad_type")}
    for mid, m in catalog.items():
        for req in ("Name", "Producer", "Type", "Connector"):
            if not m.get(req):
                out["missing_required"].append((mid, req))
        mtype = m.get("Type")
        if mtype and mtype not in valid_types:
            out["bad_type"].append((mid, mtype))
        prod = m.get("Producer")
        if prod and prod not in producers:
            out["unknown_producers"].append((mid, prod))
        # SoMs must have a Chip; mezzanines/piggybacks shouldn't
        chip = m.get("Chip")
        if chip and chip not in chips:
            out["unknown_chips"].append((mid, chip))
        for dev in (m.get("Devices") or []):
            ref = dev["Id"] if isinstance(dev, dict) else dev
            if ref and ref not in devices:
                out["unknown_devices"].append((mid, ref))
        for f in (m.get("Features") or []):
            if f not in features:
                out["unknown_features"].append((mid, f))
        for bref in (m.get("CompatibleBoards") or []):
            if bref and bref not in board_catalog:
                out["unknown_compatible"].append((mid, bref))
    return out


def _walk_board_catalog_files():
    """Yield (catalog_yml_path, producer_dir_name, family_yml_basename) for
    every family-catalog file under config/boards/<producer>/<family>.yml.

    Skips files in deeper subdirectories (those are per-board pinmaps) and
    skips directories whose name starts with `_` (e.g. `_raw/`, used for
    imported raw constraints)."""
    base = os.path.join(dir_path, "boards")
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
            if not os.path.isfile(fam_path):
                continue
            yield fam_path, prod_name, fname[:-4]


def read_boards_catalog():
    """Read every family-catalog file under config/boards/<producer>/<family>.yml
    and return {board_id: catalog_entry}.

    Each catalog_entry has BoardName, BoardProducer, PartProducer, PartFamily,
    and Part (or Parts list), optionally Programmer and BoardURL. The
    PartProducer and PartFamily fields are *injected* from the enclosing
    family-catalog file's `Producer:` and `Family:` headers — they don't
    have to be duplicated on every board entry.

    Per-board pinmaps live alongside the catalog file at
    config/boards/<producer>/<family>/<board_id>.yml."""
    return copy.deepcopy(_board_index())


# Back-compat alias.
read_boards = read_boards_catalog

_BOARD_INDEX = {}


def _board_index():
    """Validated, cached board registry; callers must not mutate its entries."""
    files = list(_walk_board_catalog_files())
    key = tuple((p, os.stat(p).st_mtime_ns, os.stat(p).st_size) for p, _pr, _f in files)
    if _BOARD_INDEX.get("key") != key:
        out = {}
        origins = {}
        for fam_path, prod_name, fam_slug in files:
            data = _parsed(fam_path)
            if not isinstance(data, dict):
                raise ConfigError("{}: board catalog must be a mapping".format(fam_path))
            if any(not isinstance(data.get(field), str) or not data[field].strip()
                   for field in ("Producer", "Family")):
                raise ConfigError("{}: board catalog needs Producer and Family"
                                  .format(fam_path))
            boards = data.get("Boards")
            if not isinstance(boards, list):
                raise ConfigError("{}: Boards must be a list".format(fam_path))
            for ordinal, b in enumerate(boards, 1):
                if (not isinstance(b, dict) or not isinstance(b.get("Id"), str) or
                        not b["Id"].strip()):
                    raise ConfigError("{}: board item {} needs a nonempty string Id"
                                      .format(fam_path, ordinal))
                if b["Id"] in out:
                    raise ConfigError("duplicate board Id {!r}: {} and {}".format(
                        b["Id"], origins[b["Id"]], fam_path))
                entry = dict(b)
                entry.setdefault("PartProducer", data.get("Producer"))
                entry.setdefault("PartFamily", data.get("Family"))
                entry["_catalog_path"] = fam_path
                entry["_producer_dir"] = prod_name
                entry["_family_dir"] = fam_slug
                out[b["Id"]] = entry
                origins[b["Id"]] = fam_path
        _BOARD_INDEX.update(key=key, value=out)
    return _BOARD_INDEX["value"]


def read_board_entry(board_id):
    """One board's catalog entry (a copy), or None."""
    entry = _board_index().get(board_id)
    return copy.deepcopy(entry) if entry is not None else None


def read_board_pinmap(board_id):
    """Load the per-board pin-map YAML for `board_id`.

    Pinmaps live at config/boards/<producer>/<family>/<board_id>.yml in
    the hierarchical layout. We consult the catalog to learn the
    producer/family directory for the given board, then look up the file.

    Returns the inner Board dict (with id, fpga, defaults, pinBanks) or
    None when no pinmap file exists for this board."""
    entry = _board_index().get(board_id)
    if entry is None:
        return None
    prod_dir = entry.get("_producer_dir")
    fam_dir = entry.get("_family_dir")
    if not prod_dir or not fam_dir:
        return None
    path = os.path.join(dir_path, "boards", prod_dir, fam_dir, board_id + ".yml")
    if not os.path.exists(path):
        return None
    data = _read_yaml_file(path)
    return (data or {}).get("Board")


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
    """Read every chip registry file under config/chips/<producer>/<family>.yml
    and return {chip_id: chip_info}.

    Each chip_info has PartProducer, PartFamily, Toolchains (chip-level
    overrides, parsed as [{id, version_constraint}, ...]), and any other
    metadata from the chip entry. Chips inherit DefaultToolchains from
    their family file when they don't declare their own."""
    out = {}
    origins = {}
    for fam_path, prod_dir, fam_slug in _walk_chip_registry_files():
        data = _read_yaml_file(fam_path)
        if not isinstance(data, dict):
            raise ConfigError("{}: chip registry must be a mapping".format(fam_path))
        if any(not isinstance(data.get(field), str) or not data[field].strip()
               for field in ("Producer", "Family")):
            raise ConfigError("{}: chip registry needs Producer and Family"
                              .format(fam_path))
        producer = data.get("Producer")
        family = data.get("Family")
        default_tcs = data.get("DefaultToolchains") or []
        chips = data.get("Chips")
        if not isinstance(chips, list):
            raise ConfigError("{}: Chips must be a list".format(fam_path))
        for ordinal, chip in enumerate(chips, 1):
            if (not isinstance(chip, dict) or not isinstance(chip.get("Id"), str) or
                    not chip["Id"].strip()):
                raise ConfigError("{}: chip item {} needs a nonempty string Id"
                                  .format(fam_path, ordinal))
            cid = chip["Id"]
            if cid in out:
                raise ConfigError("duplicate chip Id {!r}: {} and {}".format(
                    cid, origins[cid], fam_path))
            entry = dict(chip)
            entry["PartProducer"] = producer
            entry["PartFamily"] = family
            # Chip's Toolchains override the family DefaultToolchains
            if "Toolchains" not in entry or not entry["Toolchains"]:
                entry["Toolchains"] = list(default_tcs)
            entry["_registry_path"] = fam_path
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
    import re as _re
    m = _re.match(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\[([^\]]+)\])?$", str(ref))
    if not m:
        raise ConfigError("Malformed versioned reference: {r!r}".format(r=ref))
    return m.group(1), m.group(2)


def programmers_for_board(board_id, *, catalog=None, chips=None, programmers=None):
    """Compute the set of programmers usable on the given board.

    Resolution rules (additive):
      1. **Bundled / chip-tied via toolchain:** any programmer whose
         `Bundled:` toolchain id appears in the board's chip's
         `Toolchains` list.
      2. **Third-party with explicit chip-family support:** any programmer
         whose `SupportedFamilies:` includes (chip.PartProducer, chip.PartFamily)
         AND whose `RequiresBridge:` is satisfied by the board's `Bridges:`
         (or which has no bridge requirement).
      3. **Bootloader-tied:** any programmer whose `RequiresBootloader:`
         matches the board's `Bootloader:` (if set).
      4. **Board-explicit:** every entry in the board's `ExtraProgrammers:`
         list (parsed for version constraints).

    Returns a list of programmer entries (full dicts from programmers.yml)
    in roughly resolution-rule order. Callers that want a single "preferred"
    programmer can pick the first entry, or honor the board's `Programmer:`
    field as the explicit default.
    """
    if catalog is None: catalog = read_boards_catalog()
    if chips is None: chips = read_chips()
    if programmers is None: programmers = read_programmers()

    board = catalog.get(board_id)
    if board is None:
        raise ConfigError("Unknown board: {b}".format(b=board_id))

    # Resolve the board's chip(s). Single-Chip or multi-Chips (variants).
    chip_ids = []
    if board.get("Chip"):
        chip_ids.append(board["Chip"])
    elif board.get("Chips"):
        for entry in board["Chips"]:
            if isinstance(entry, dict):
                if entry.get("Id"):
                    chip_ids.append(entry["Id"])
            else:
                chip_ids.append(entry)
    bridges = set(board.get("Bridges") or [])
    bootloader = board.get("Bootloader")

    # Collect the union of toolchain ids supported by ANY of the board's chips.
    chip_toolchain_ids = set()
    chip_pp = None
    chip_pf = None
    for cid in chip_ids:
        chip = chips.get(cid)
        if chip is None:
            log.warning("Board %s references unknown chip %s", board_id, cid)
            continue
        chip_pp = chip.get("PartProducer")
        chip_pf = chip.get("PartFamily")
        for ref in chip.get("Toolchains", []):
            tc_id, _ = parse_versioned_ref(ref)
            chip_toolchain_ids.add(tc_id)

    result = []
    seen = set()
    def add(p):
        if p["Id"] not in seen:
            result.append(p)
            seen.add(p["Id"])

    # Rule 1: bundled vendor programmers via toolchain match
    for pid, p in programmers.items():
        if p.get("Bundled") and p["Bundled"] in chip_toolchain_ids:
            # If the programmer requires a specific bridge, check it
            req = p.get("RequiresBridge")
            if req and req not in bridges:
                continue
            add(p)
    # Rule 2: third-party with explicit family support
    for pid, p in programmers.items():
        if p.get("Bundled"):
            continue
        sf = p.get("SupportedFamilies") or []
        match = any(
            (entry.get("Producer") == chip_pp and entry.get("Family") == chip_pf)
            for entry in sf
        )
        if not match:
            continue
        req = p.get("RequiresBridge")
        if req and req not in bridges:
            continue
        add(p)
    # Rule 3: bootloader-tied
    if bootloader:
        for pid, p in programmers.items():
            if p.get("RequiresBootloader") == bootloader:
                add(p)
    # Rule 4: board-explicit ExtraProgrammers
    for ref in board.get("ExtraProgrammers") or []:
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
    """One configuration: its own file when it is named after its id (all
    are), else a search of them all; None when there is none."""
    path = os.path.join(dir_path, "configurations", str(configuration_id) + ".yml")
    if os.path.isfile(path):
        item = (_read_yaml_file(path) or {}).get("Configuration") or {}
        if item.get("id") == configuration_id:
            return item
    return read_configurations().get(configuration_id)


def read_configurations():
    """Read every configuration under config/configurations/."""
    return _load_yaml_dir("configurations", "Configuration", "id")


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
    configuration file changes (the files are parsed once, not copied)."""
    base = os.path.join(dir_path, "configurations")
    paths = [os.path.join(base, n) for n in sorted(os.listdir(base)) if n.endswith(".yml") and not n.startswith("_")]
    key = tuple((p, os.stat(p).st_mtime_ns, os.stat(p).st_size) for p in paths)
    if _ALIAS_INDEX.get("key") != key:
        out = {}
        for p in paths:
            cfg = (_parsed(p) or {}).get("Configuration") or {}
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
    chip_ids = []
    if board.get("Chip"):
        chip_ids.append(board["Chip"])
    elif board.get("Chips"):
        for entry in board["Chips"]:
            if isinstance(entry, dict) and entry.get("Id"):
                chip_ids.append(entry["Id"])
            elif isinstance(entry, str):
                chip_ids.append(entry)
    if not chip_ids:
        raise ConfigError(
            "Board {b} has no Chip / Chips field — can't determine toolchain support"
            .format(b=board_id))
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
        for ref in chip.get("Toolchains", []):
            tc_id, constraint = parse_versioned_ref(ref)
            if tc_id == toolchain_id:
                constraints.append(constraint or "*")
    return constraints


def is_compatible(boards, chips, board_id, toolchain_id):
    """Whether the board lists this toolchain, before version admission."""
    return bool(toolchain_constraints(boards, chips, board_id, toolchain_id))


def require_toolchain_version(toolchain):
    """Reject an installation that conflicts with its pin or chip limits."""
    configured = toolchain.get("ConfiguredVersion")
    detected = toolchain.get("DetectedInstallVersion")
    if toolchain.get("DetectSource") and configured:
        if not detected:
            raise ConfigError("Toolchain '{t}' version could not be determined for "
                              "the detected installation; configured version is {v}"
                              .format(t=toolchain["Id"], v=configured))
        if str(configured).casefold() != str(detected).casefold():
            raise ConfigError("Toolchain '{t}' detected version {found} conflicts "
                              "with configured version {expected}"
                              .format(t=toolchain["Id"], found=detected,
                                      expected=configured))
    version = detected if toolchain.get("DetectSource") else configured
    constraints = toolchain.get("ChipVersionConstraints") or []
    if not constraints:
        raise ConfigError("Toolchain '{t}' has no chip compatibility evidence"
                          .format(t=toolchain["Id"]))
    from tools import toolchain_detect
    try:
        matches = [toolchain_detect.matches_version(version, constraint)
                   for constraint in constraints]
        if any(matches):
            return
    except ValueError as exc:
        raise ConfigError("Toolchain '{t}' has invalid chip version constraint: {e}"
                          .format(t=toolchain["Id"], e=exc)) from exc
    raise ConfigError("Toolchain '{t}' version {v} does not meet chip constraint(s) {c}"
                      .format(t=toolchain["Id"], v=version or "unknown",
                              c=", ".join(constraints)))


def resolve_toolchain_install(toolchain):
    """Copy of a toolchains.yml entry with the install resolved by
    tools/toolchain_detect.py: the `InstallDir` pin when it exists, else the
    vendor environment variable, PATH, then the default install parents.
    Adds `BinDirs` (for PATH), `Bins`, `DetectSource`, `DetectNotes`, and
    separate configured and install-path version evidence;
    leaves `InstallDir` as written when nothing is found so the driver's own
    error message still names it."""
    tc = dict(toolchain)
    tc["ConfiguredVersion"] = str(tc["Version"]) if tc.get("Version") else None
    try:
        from tools import toolchain_detect
    except ImportError:              # config/ imported without the repo root on sys.path
        return tc
    det = toolchain_detect.detect(tc["Id"], pin=tc.get("InstallDir"))
    tc["DetectSource"] = det.source
    tc["DetectedInstallVersion"] = str(det.version) if det.found and det.version else None
    tc["DetectNotes"] = list(det.notes)
    tc["BinDirs"] = list(det.bin_dirs)
    tc["Bins"] = dict(det.bins)
    if det.found:
        if det.install_dir:
            tc["InstallDir"] = det.install_dir
        if det.version and not tc.get("Version"):
            tc["Version"] = det.version
    return tc


def resolve_configuration(configuration_id, configuration=None, toolchain=None, part=None):
    """
    Look up a configuration by id and return a fully-resolved bundle:

        {
          "configuration": <Configuration dict>,
          "board":         <catalog entry from boards.yml>,
          "board_pinmap":  <pinBanks dict from config/boards/<id>.yml>,
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
    that id (an unsaved setup in the board editor); its profile, if any,
    still applies.

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

    boards = _board_index()                 # read-only; the board is copied below
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
    # Resolve the chip part number — toolchain drivers expect `board["Part"]`
    # (or `board["Parts"]` for multi-variant boards). We inject these by
    # looking up the chip(s) the board references.
    board_resolved = copy.deepcopy(boards[board_id])
    selected_chip_id = None
    if board_resolved.get("Chip"):
        cid = board_resolved["Chip"]
        selected_chip_id = cid
        chip = chips.get(cid)
        if chip is None:
            raise ConfigError("Board '{b}' references unknown chip '{c}'"
                              .format(b=board_id, c=cid))
        board_resolved["Part"] = chip.get("Part") or cid
    elif board_resolved.get("Chips"):
        parts_list = []
        for entry in board_resolved["Chips"]:
            if isinstance(entry, dict):
                cid = entry.get("Id")
                name = entry.get("Name")
            else:
                cid, name = entry, None
            chip = chips.get(cid)
            if chip is None:
                raise ConfigError("Board '{b}' references unknown chip '{c}'"
                                  .format(b=board_id, c=cid))
            p = {"Part": chip.get("Part") or cid, "Id": cid}
            if name:
                p["Name"] = name
            parts_list.append(p)
        board_resolved["Parts"] = parts_list
        # Multi-die boards (Arty A7 35T/100T, Nexys A7 50T/100T, OrangeCrab
        # 25F/85F): the configuration must say which die it targets. Without
        # `part:` every driver used to fall back to Parts[0] silently.
        wanted = cfg.get("part")
        if wanted is not None:
            w = str(wanted).strip().lower()
            chosen = None
            for p in parts_list:
                if w in {str(p.get("Name", "")).lower(), str(p["Part"]).lower(), str(p["Id"]).lower()}:
                    chosen = p
                    break
            if chosen is None:
                raise ConfigError(
                    "Configuration '{c}': part: {w!r} is not one of the board's chips ({opts})"
                    .format(c=configuration_id, w=wanted,
                            opts=", ".join("{}={}".format(p.get("Name", "?"), p["Id"]) for p in parts_list)))
            board_resolved["Part"] = chosen["Part"]
            board_resolved["PartName"] = chosen.get("Name")
            selected_chip_id = chosen["Id"]
        else:
            log.warning("Configuration '%s': board '%s' has %d chips but no part: is set; "
                        "toolchains will default to %s (audit code PART)",
                        configuration_id, board_id, len(parts_list), parts_list[0]["Part"])
            selected_chip_id = parts_list[0]["Id"]

    constraints = toolchain_constraints(boards, chips, board_id, toolchain_id,
                                        selected_chip_id=selected_chip_id)
    if not constraints:
        raise ConfigError("Configuration '{c}': toolchain '{t}' is not compatible with "
                          "selected chip '{chip}' on board '{b}'"
                          .format(c=configuration_id, t=toolchain_id,
                                  chip=selected_chip_id, b=board_id))
    resolved_toolchain = resolve_toolchain_install(toolchains[toolchain_id])
    resolved_toolchain["ChipVersionConstraints"] = constraints

    board_pinmap = read_board_pinmap(board_id)
    if board_pinmap is None:
        raise ConfigError("Board '{b}' has no pinmap under config/boards/<producer>/<family>/ — "
                          "add its pinmap".format(b=board_id))
    _apply_pin_overrides(configuration_id, cfg, board_pinmap)
    _apply_io_overrides(configuration_id, cfg, board_pinmap)
    if board_resolved.get("Part"):
        tool_part = _tool_part(board_resolved["Part"], toolchain_id)
        if tool_part != board_resolved["Part"]:
            board_resolved["PartOrderingCode"] = board_resolved["Part"]
            board_resolved["Part"] = tool_part

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
            # `lab_bits: {leds: [0, 1, ...]}` — which bits of the design's bus
            # this provider occupies (a TM1638 can share the lab's led/key
            # buses with the board's own LEDs and keys instead of extending
            # them); absent = the next free bits, in attach order
            "lab_bits":      entry.get("lab_bits", {}) or {},
            # position in the configuration's attach list (a profile may drop
            # attaches; tools/trace.py relates providers back to it)
            "attach_index":  attach_index,
        })

    # design-wiring profile (config/profiles/<id>.yml, config/profile.py): how
    # the example designs use this hardware — reset policy, bus composition,
    # design clock, bit order, pins that follow the reset — applied on top of
    # the configuration unless UNIFPGA_PROFILE=0.
    try:
        from config import profile
    except ImportError:              # config/ imported without the repo root on sys.path
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from config import profile
    if profile.enabled():
        cfg = profile.apply(cfg, attached, profile.for_toolchain(profile.load(rig_id), toolchain_id))

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
        "board":         board_resolved,
        "board_pinmap":  board_pinmap,
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


def _apply_pin_overrides(configuration_id, cfg, pinmap):
    """Apply the configuration's `pin_overrides:` to its (private copy of the)
    board pinmap. A variant that wires a header differently from the board's
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
    banks = pinmap.setdefault("pinBanks", {})
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


def _apply_io_overrides(configuration_id, cfg, pinmap):
    """Apply the configuration's `io_overrides:` (IO standard per bank, sub-key
    or pin) to its private pinmap copy. The Gowin variants type pins per
    variant (the Tang Nano 9K HDMI variants put LVCMOS33 on CLK, the LCD
    variants type nothing), so the pinmap keeps what every variant agrees on
    and each configuration carries its own additions:

        io_overrides:
          clk:            LVCMOS33      # whole bank
          onboard_lcd.r:  LVCMOS33      # one sub-key
          "gpio[0]":      LVCMOS33      # one pin
    """
    overrides = cfg.get("io_overrides") or {}
    if not overrides:
        return
    banks = pinmap.setdefault("pinBanks", {})
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
        raise ConfigError("No configurations found in config/configurations/")
    boards = read_boards_catalog()

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
            name=b.get("BoardName", bid),
            producer=b.get("PartProducer", "?"),
            family=b.get("PartFamily", "?"),
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
        print("\nConfigurations for {}:\n".format(boards[board_id].get("BoardName", board_id)))
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

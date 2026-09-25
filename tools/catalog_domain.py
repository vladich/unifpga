"""First-pass identity and direct-reference checks for a Git catalog capture.

This is intentionally a scoped validation pass. It does not replace the
configuration resolver, pin/electrical review, setup roundtrip, or HDL checks.
"""

from collections import defaultdict
import reprlib

from config.references import parse_versioned_ref


DIRECTORY_RECORDS = {
    "capabilities": ("Capability", "id", "capability"),
    "peripherals": ("Peripheral", "id", "peripheral"),
    "configurations": ("Configuration", "id", "configuration"),
    "setups": ("Setup", "id", "setup"),
    "layouts": ("Layout", "board", "layout"),
    "modules": ("Module", "id", "module"),
    "profiles": ("Profile", "configuration", "profile"),
}
SINGLE_REGISTRIES = {
    "toolchains.yml": ("Toolchains", "toolchain"),
    "programmers.yml": ("Programmers", "programmer"),
    "features.yml": ("Features", "feature"),
    "board_features.yml": ("Features", "legacy_board_feature"),
    "peripheral_devices.yml": ("Devices", "peripheral_device"),
    "board_producers.yml": ("Producers", "board_producer"),
}
ROOT_MAPPINGS = {"connectors.yml": "Connectors",
                 "vendor_constraints.yml": "VendorConstraints"}
REQUIRED_KINDS = ("board", "chip", "configuration", "toolchain",
                  "peripheral", "capability")
MAX_FINDINGS = 10000
MAX_DETAIL_LENGTH = 512
CHECKS = ["root_shape", "identity", "duplicate_identity", "filename_identity",
          "board_chip", "configuration_board_toolchain_peripheral",
          "setup_board_toolchain_module", "profile_configuration_peripheral",
          "module_peripheral", "peripheral_capability", "layout_board",
          "board_producer_feature_device_programmer",
          "mezzanine_producer_chip_feature_device_carrier",
          "device_feature_peripheral", "feature_capability",
          "chip_family_toolchain", "programmer_bundled_toolchain"]


def _record_id(value):
    return value.get("Id") if isinstance(value, dict) else value


def _versioned_id(value):
    return parse_versioned_ref(value)[0]


def validate_catalog_documents(documents):
    """Return bounded, deterministic findings for the currently covered kinds.

    ``documents`` maps catalog-root-relative paths to parsed YAML from
    ``capture_catalog_revision(..., with_documents=True)``. Unknown paths fail
    and are listed as unexamined; callers must not claim a complete domain pass.
    """
    findings = []
    records = defaultdict(dict)
    unexamined = []

    def finding(code, path, detail):
        if len(findings) == MAX_FINDINGS:
            findings.append({"code": "finding_limit", "path": "",
                             "detail": "domain finding limit reached; report is incomplete",
                             "severity": "error"})
        if len(findings) < MAX_FINDINGS:
            findings.append({"code": code, "path": path,
                             "detail": detail[:MAX_DETAIL_LENGTH],
                             "severity": "error"})

    def register(kind, identifier, path, item, expected=None):
        if not isinstance(identifier, str) or not identifier.strip():
            finding("invalid_identity", path, "{} requires a nonempty string identity".format(kind))
            return
        if expected is not None and identifier != expected:
            finding("filename_identity", path, "{} identity {!r} differs from filename {!r}"
                    .format(kind, identifier, expected))
        prior = records[kind].get(identifier)
        if prior is not None:
            finding("duplicate_identity", path, "{} {!r} also registered at {}"
                    .format(kind, identifier, prior[0]))
            return
        records[kind][identifier] = (path, item)

    for path in sorted(documents):
        data = documents[path]
        parts = path.split("/")
        if path in SINGLE_REGISTRIES:
            key, kind = SINGLE_REGISTRIES[path]
            items = data.get(key) if isinstance(data, dict) else None
            if not isinstance(items, list):
                finding("invalid_root", path, "{} must be a list".format(key))
                continue
            for ordinal, item in enumerate(items, 1):
                if not isinstance(item, dict):
                    finding("invalid_record", path, "{} item {} must be a mapping".format(key, ordinal))
                    continue
                register(kind, item.get("Id"), path, item)
        elif path in ROOT_MAPPINGS:
            key = ROOT_MAPPINGS[path]
            if not isinstance(data, dict) or not isinstance(data.get(key), dict):
                finding("invalid_root", path, "{} must be a mapping".format(key))
        elif len(parts) == 2 and parts[0] in DIRECTORY_RECORDS:
            key, field, kind = DIRECTORY_RECORDS[parts[0]]
            item = data.get(key) if isinstance(data, dict) else None
            if not isinstance(item, dict):
                finding("invalid_root", path, "{} must be a mapping".format(key))
                continue
            register(kind, item.get(field), path, item, parts[-1][:-4])
        elif len(parts) == 3 and parts[0] in ("boards", "chips", "mezzanines"):
            key = {"boards": "Boards", "chips": "Chips",
                   "mezzanines": "Mezzanines"}[parts[0]]
            kind = {"boards": "board", "chips": "chip",
                    "mezzanines": "mezzanine"}[parts[0]]
            items = data.get(key) if isinstance(data, dict) else None
            if not isinstance(items, list):
                finding("invalid_root", path, "{} must be a list".format(key))
                continue
            if kind == "chip":
                register("chip_family", path, path, data)
            for ordinal, item in enumerate(items, 1):
                if not isinstance(item, dict):
                    finding("invalid_record", path, "{} item {} must be a mapping".format(key, ordinal))
                    continue
                register(kind, item.get("Id"), path, item)
        elif len(parts) == 4 and parts[0] == "boards":
            item = data.get("Board") if isinstance(data, dict) else None
            if not isinstance(item, dict):
                finding("invalid_root", path, "Board must be a mapping")
                continue
            register("pinmap", item.get("id"), path, item, parts[-1][:-4])
        else:
            unexamined.append(path)
            finding("unsupported_catalog_path", path,
                    "no domain registration rule for this catalog source")

    for kind in REQUIRED_KINDS:
        if not records[kind]:
            finding("missing_kind", "", "catalog has no {} records".format(kind))

    def reference(source_kind, field, target_kind, *, collection=False,
                  extract=None):
        for identity, (path, item) in records[source_kind].items():
            values = item.get(field)
            if values is None:
                continue
            if collection:
                if not isinstance(values, list):
                    finding("invalid_reference", path, "{} {} must be a list"
                            .format(source_kind, field))
                    continue
            else:
                values = [values]
            for value in values:
                try:
                    target = extract(value) if extract else value
                except ValueError:
                    target = None
                if not isinstance(target, str) or not target.strip():
                    finding("invalid_reference", path, "{} {} has an invalid reference {}"
                            .format(source_kind, field, reprlib.repr(value)))
                elif target not in records[target_kind]:
                    finding("unknown_reference", path, "{} {!r} {} refers to absent {} {!r}"
                            .format(source_kind, identity, field, target_kind, target))

    reference("board", "Chip", "chip")
    reference("board", "Chips", "chip", collection=True, extract=_record_id)
    reference("board", "BoardProducer", "board_producer")
    reference("board", "Programmer", "programmer")
    reference("board", "ExtraProgrammers", "programmer", collection=True,
              extract=_versioned_id)
    reference("board", "Features", "feature", collection=True)
    reference("board", "Devices", "peripheral_device", collection=True,
              extract=_record_id)
    reference("mezzanine", "Producer", "board_producer")
    reference("mezzanine", "Chip", "chip")
    reference("mezzanine", "Features", "feature", collection=True)
    reference("mezzanine", "Devices", "peripheral_device", collection=True,
              extract=_record_id)
    reference("mezzanine", "CompatibleBoards", "board", collection=True)
    reference("mezzanine", "DefaultCarrier", "board")
    reference("peripheral_device", "Feature", "feature")
    reference("peripheral_device", "PeripheralDrivers", "peripheral", collection=True)
    reference("feature", "Capabilities", "capability", collection=True)
    reference("programmer", "Bundled", "toolchain")
    reference("chip_family", "DefaultToolchains", "toolchain", collection=True,
              extract=_versioned_id)
    reference("chip", "Toolchains", "toolchain", collection=True,
              extract=_versioned_id)
    reference("configuration", "board", "board")
    reference("configuration", "toolchain", "toolchain")
    reference("configuration", "toolchains", "toolchain", collection=True)
    reference("setup", "board", "board")
    reference("setup", "toolchain", "toolchain")
    reference("setup", "toolchains", "toolchain", collection=True)
    reference("profile", "configuration", "configuration")
    reference("layout", "board", "board")
    reference("module", "peripheral", "peripheral")
    reference("pinmap", "id", "board")

    for kind in ("configuration", "profile"):
        for identity, (path, item) in records[kind].items():
            attaches = item.get("attach") or []
            if not isinstance(attaches, list):
                finding("invalid_reference", path, "{} attach must be a list".format(kind))
                continue
            for attach in attaches:
                if not isinstance(attach, dict) or not isinstance(attach.get("peripheral"), str):
                    finding("invalid_reference", path, "{} attach needs a peripheral".format(kind))
                elif attach["peripheral"] not in records["peripheral"]:
                    finding("unknown_reference", path, "{} {!r} attaches absent peripheral {!r}"
                            .format(kind, identity, attach["peripheral"]))
    for identity, (path, item) in records["setup"].items():
        uses = item.get("use") or []
        if not isinstance(uses, list):
            finding("invalid_reference", path, "setup use must be a list")
            continue
        for use in uses:
            if isinstance(use, dict) and "module" in use:
                name = use["module"]
                if not isinstance(name, str) or name not in records["module"]:
                    finding("unknown_reference", path, "setup {!r} uses absent module {!r}"
                            .format(identity, name))
    for identity, (path, item) in records["peripheral"].items():
        provides = item.get("provides") or []
        if not isinstance(provides, list):
            finding("invalid_reference", path, "peripheral provides must be a list")
            continue
        for provided in provides:
            name = provided.get("capability") if isinstance(provided, dict) else None
            if not isinstance(name, str) or name not in records["capability"]:
                finding("unknown_reference", path, "peripheral {!r} provides absent capability {!r}"
                        .format(identity, name))

    findings.sort(key=lambda row: (row["path"], row["code"], row["detail"]))
    return {"scope": "identity-and-direct-references/v1", "checks": CHECKS,
            "status": "failed" if findings else "partial_passed",
            "counts": {kind: len(items) for kind, items in sorted(records.items())},
            "unexamined": unexamined, "findings": findings}

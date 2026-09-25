"""Pure syntax helpers for references shared by live and Git-bound readers."""

import re


_VERSIONED_REFERENCE = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*)(?:\[([^\[\]\x00-\x1f\x7f]+)\])?")


def parse_versioned_ref(ref):
    """Return (catalog ID, optional constraint) for a tool or programmer ref."""
    match = _VERSIONED_REFERENCE.fullmatch(ref) if isinstance(ref, str) else None
    if match is None:
        raise ValueError("Malformed versioned reference: {!r}".format(ref))
    return match.group(1), match.group(2)

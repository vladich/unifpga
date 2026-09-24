"""
config/overlay.py -- the patches a rig's `for_toolchain:` holds.

One rig (setup, configuration, profile) builds with several toolchains; where
a toolchain needs something else, `for_toolchain: {<toolchain>: <patch>}`
says only what changes. A setup's uses and a configuration's attaches carry
their own `for_toolchain` (so a patch moves with its entry when the list is
edited); the rig's top-level one patches everything else. A patch:

    a mapping patch on a mapping    merges key by key; a key set to null is removed
    a mapping patch on a list       patches the elements it names by position
                                    ({3: {...}} changes the fourth element)
    {(replace): <value>}            the value, exactly (another key order, or null)
    anything else                   replaces the value

diff(a, b) is the smallest patch that turns a into b (apply(a, diff(a, b))
equals b, key order included); UNCHANGED when there is nothing to patch.
"""

import copy

UNCHANGED = object()
REPLACE = "(replace)"


def _ordered(value):
    if isinstance(value, dict):
        return [(k, _ordered(v)) for k, v in value.items()]
    if isinstance(value, list):
        return [_ordered(v) for v in value]
    return value


def same(a, b):
    """Equal, mapping keys in the same order."""
    return _ordered(a) == _ordered(b)


def apply(base, patch):
    """`base` (not modified) with `patch` applied."""
    if isinstance(patch, dict) and list(patch) == [REPLACE]:
        return copy.deepcopy(patch[REPLACE])
    if isinstance(patch, dict) and isinstance(base, dict):
        out = copy.deepcopy(base)
        for k, v in patch.items():
            if v is None:
                out.pop(k, None)
            elif k in out:
                out[k] = apply(out[k], v)
            else:
                out[k] = copy.deepcopy(v)
        return out
    if isinstance(patch, dict) and isinstance(base, list):
        out = copy.deepcopy(base)
        for k, v in patch.items():
            if not isinstance(k, int) or not 0 <= k < len(out):
                raise ValueError("a list patch names element {!r} of a {}-element list".format(k, len(out)))
            out[k] = apply(out[k], v)
        return out
    return copy.deepcopy(patch)


def diff(a, b, keep_order=True):
    """The patch that turns `a` into `b`, or UNCHANGED. keep_order=False: the
    order of mapping keys does not matter (a patch appends new keys)."""
    eq = same if keep_order else (lambda x, y: x == y)
    if eq(a, b):
        return UNCHANGED
    patch = None
    if isinstance(a, dict) and isinstance(b, dict):
        patch = {}
        for k, v in b.items():
            if k not in a:
                patch[k] = copy.deepcopy(v)
            else:
                d = diff(a[k], v, keep_order)
                if d is not UNCHANGED:
                    patch[k] = d
        for k in a:
            if k not in b:
                patch[k] = None
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        patch = {}
        for i, (x, y) in enumerate(zip(a, b)):
            d = diff(x, y, keep_order)
            if d is not UNCHANGED:
                patch[i] = d
    if patch is not None and eq(apply(a, patch), b):
        return patch
    if b is None or isinstance(b, dict):
        # null means "remove" and a mapping means "merge": neither says "be exactly this"
        return {REPLACE: copy.deepcopy(b)}
    return copy.deepcopy(b)


def select(doc, key, list_key):
    """`doc` as `key` builds it: its top-level `for_toolchain[key]` patch and
    each `list_key` element's own applied, every `for_toolchain` removed."""
    doc = copy.deepcopy(doc)
    top = doc.pop("for_toolchain", None) or {}
    if key in top:
        doc = apply(doc, top[key])
    if doc.get(list_key) is not None:
        items = []
        for item in doc[list_key]:
            own = item.pop("for_toolchain", None) or {} if isinstance(item, dict) else {}
            items.append(apply(item, own[key]) if key in own else item)
        doc[list_key] = items
    return doc


def split(base, others, list_key):
    """`base` with `for_toolchain` patches that turn it into each of `others`
    ({key: doc}): a patch on each `list_key` element that changes, one at the
    top for the rest. The lists must be as long as the base's."""
    out = copy.deepcopy(base)
    top = {}
    for key, other in others.items():
        items, their = base.get(list_key) or [], other.get(list_key) or []
        if len(items) != len(their):
            raise ValueError("{}: {} {} entries against {}".format(key, len(their), list_key, len(items)))
        for k, (a, b) in enumerate(zip(items, their)):
            d = diff(a, b)
            if d is not UNCHANGED:
                out[list_key][k].setdefault("for_toolchain", {})[key] = d
        rest = {k: v for k, v in other.items() if k != list_key}
        d = diff({k: v for k, v in base.items() if k != list_key}, rest)
        if d is not UNCHANGED:
            top[key] = d
    if top:
        out["for_toolchain"] = top
    return out

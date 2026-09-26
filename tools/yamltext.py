"""The one way unifpga writes YAML by hand (the setup writer, the drafter, the
inventory importer, the configuration view): block style, so a file reads as
YAML and never as JSON dropped into it.

  * a mapping is always a block, one `key: value` per line, nested mappings
    indented by two;
  * a list of scalars is a flow list on the key's line (`pins: [P19, R19]`,
    `design_bits: [1, 3, 2]`): short, and the natural way to read a pin list;
  * a list holding a mapping or a list is a block list (`- ` items, a
    mapping's first key on the dash line);
  * a scalar is written as PyYAML writes it: plain when it reads back as the
    same value, else quoted (single quotes, double where an escape is needed).

Comments are the caller's: the writers splice these lines into files whose
comments they keep.
"""

import re

import yaml

_DOC_END = re.compile(r"\n\.\.\.\s*$")


def scalar(v):
    """One scalar as YAML writes it (an int stays an int, '5' stays a string)."""
    text = yaml.safe_dump(v, default_flow_style=True, width=1 << 20, allow_unicode=True)
    return _DOC_END.sub("", text).strip()


def inline(v):
    """A list of scalars as a flow list on one line; a scalar as itself."""
    if isinstance(v, list):
        return yaml.safe_dump(v, default_flow_style=True, width=1 << 20, allow_unicode=True).strip()
    return scalar(v)


def _scalars_only(items):
    return all(not isinstance(x, (dict, list)) for x in items)


def block(value, indent=0):
    """The lines of `value` in block style, indented by `indent` spaces: what
    follows a `key:` (a mapping) or a `- ` (an item)."""
    pad = " " * indent
    out = []
    if isinstance(value, dict):
        for k, v in value.items():
            out += entry(k, v, indent)
    elif isinstance(value, list):
        for item in value:
            if (isinstance(item, dict) and item) or (isinstance(item, list) and item and not _scalars_only(item)):
                lines = block(item, indent + 2)
                out.append(pad + "- " + lines[0][indent + 2:])
                out += lines[1:]
            else:
                out.append(pad + "- " + inline(item))
    else:
        out.append(pad + scalar(value))
    return out


def entry(key, value, indent=0):
    """The lines of one mapping entry `key: value` at `indent`."""
    pad = " " * indent
    if isinstance(value, dict):
        if not value:
            return [pad + scalar(key) + ": {}"]
        return [pad + scalar(key) + ":"] + block(value, indent + 2)
    if isinstance(value, list):
        if not value:
            return [pad + scalar(key) + ": []"]
        if _scalars_only(value):
            return [pad + scalar(key) + ": " + inline(value)]
        return [pad + scalar(key) + ":"] + block(value, indent)
    return [pad + scalar(key) + ": " + scalar(value)]

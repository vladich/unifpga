"""Code names no catalogue entry: every board, chip, configuration, setup,
layout, peripheral and module is data the code reads, never a literal it
tests for. (Capabilities are exempt: they are the virtual device's schema,
which the engine implements, clock and reset and gpio included.)

The check is exact: a string constant in Python, or a quoted string in the
editor's JavaScript, that equals an id."""

import ast
import glob
import logging
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
logging.disable(logging.CRITICAL)

from config import init as config_init    # noqa: E402
from tools import setup as su             # noqa: E402

CODE = ["synthesize.py"] + sorted(glob.glob(os.path.join("config", "*.py")) + glob.glob(os.path.join("tools", "**", "*.py"), recursive=True)
                                  + glob.glob(os.path.join("toolchains", "**", "*.py"), recursive=True))
SCRIPTS = sorted(glob.glob(os.path.join("tools", "studio", "*.js")))


def _catalogue_ids():
    ids = {}
    sources = [("board", config_init.read_boards_catalog()), ("chip", config_init.read_chips()),
               ("configuration", config_init.read_configurations()), ("setup", su.read_setups()),
               ("layout", su.read_layouts()), ("peripheral", config_init.read_peripherals()), ("module", su.read_modules())]
    for kind, entries in sources:
        for k in entries:
            ids.setdefault(str(k), kind)
    return ids


def test_code_names_no_catalogue_entry():
    ids = _catalogue_ids()
    assert len(ids) > 500
    found = []
    for rel in CODE:
        with open(os.path.join(REPO, rel), encoding="utf-8") as f:
            tree = ast.parse(f.read(), rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in ids:
                found.append("{}:{}: {} {!r}".format(rel, node.lineno, ids[node.value], node.value))
    for rel in SCRIPTS:
        with open(os.path.join(REPO, rel), encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                for m in re.finditer(r"([\"'])([\w.\-/]+)\1", line):
                    if m.group(2) in ids:
                        found.append("{}:{}: {} {!r}".format(rel, n, ids[m.group(2)], m.group(2)))
    assert not found, "catalogue ids in code (read them from data instead):\n" + "\n".join(found)

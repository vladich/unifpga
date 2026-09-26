"""tools/yamltext: the one block-style emitter behind every YAML unifpga
writes (rig files, the drawn section of a board, the inventory's banks, the
configuration view), and the repository rule it enforces: a mapping is a
block, never `{a: b}` dropped into the file."""

import glob
import os
import re

import pytest
import yaml

from tools import setup as su
from tools import yamltext as yt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUOTED = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"\\]|\\.)*\"")
COMMENT = re.compile(r"(?:^|\s)#.*$")


def uncommented(line):
    """The line without its trailing comment (a `#` inside quotes is not one)."""
    m = COMMENT.search(QUOTED.sub(lambda q: "Q" * len(q.group(0)), line))
    return (line[:m.start()] if m else line).rstrip()


def flow_mappings(text):
    """The lines of `text` that open a flow mapping outside quotes and
    comments (an empty `{}` is allowed: it is the only spelling of an empty
    mapping on one line)."""
    out = []
    for n, line in enumerate(text.split("\n"), 1):
        bare = COMMENT.sub("", QUOTED.sub("Q", line))
        if re.search(r"(^|[\s\[,:])\{(?!\})", bare):
            out.append("{}: {}".format(n, line.strip()))
    return out


@pytest.mark.parametrize("value", ["pmod_ja[0]", "1'b1", "8'h20", "p. 3", "5", 5, 5.5, True, None, "", "yes", "null", "a: b",
                                   "a # b", "- x", "[x]", "{y}", "*", "@x", "0644", "1e3", "it's", "with \"quotes\"", "über"])
def test_a_scalar_reads_back_as_itself(value):
    assert yaml.safe_load(yt.scalar(value)) == value
    assert "\n" not in yt.scalar(value)


def test_plain_when_it_can_be_quoted_only_when_it_must():
    assert yt.scalar("pmod_ja[0]") == "pmod_ja[0]"
    assert yt.scalar("PMOD0 (J9)") == "PMOD0 (J9)"
    assert yt.scalar("5") == "'5'" and yt.scalar(5) == "5"
    assert yt.scalar("a: b") == "'a: b'"


def test_an_entry_is_a_block_for_a_mapping_and_a_flow_list_for_scalars():
    assert yt.entry("pins", ["T18", "R18"], 6) == ["      pins: [T18, R18]"]
    assert yt.entry("design_bits", [1, 3, None], 2) == ["  design_bits: [1, 3, null]"]
    assert yt.entry("wires", {"SCK": "pmod_jd.[4]", "L/R": "pmod_jd.[6]"}, 6) == \
        ["      wires:", "        SCK: pmod_jd.[4]", "        L/R: pmod_jd.[6]"]
    assert yt.entry("pins", {"5": "pmod_0[6]", 6: "pmod_0[7]"}, 0) == ["pins:", "  '5': pmod_0[6]", "  6: pmod_0[7]"]
    assert yt.entry("empty", {}, 0) == ["empty: {}"] and yt.entry("none", [], 0) == ["none: []"]
    assert yt.entry("note", "a: b", 2) == ["  note: 'a: b'"]


def test_a_list_holding_mappings_or_lists_is_a_block_list():
    lines = yt.entry("signals", [{"name": "req", "type": "scalar"}, {"name": "addr", "type": "bus", "pins": ["A1", "A2"]}], 2)
    assert lines == ["  signals:", "  - name: req", "    type: scalar", "  - name: addr", "    type: bus", "    pins: [A1, A2]"]
    assert yt.entry("rows", [[1, 2], [3, 4]], 4) == ["    rows:", "    - [1, 2]", "    - [3, 4]"]
    nested = yt.entry("value", ["bits_r", {"param": "color_depth", "digit": 0}], 0)
    assert nested == ["value:", "- bits_r", "- param: color_depth", "  digit: 0"]


@pytest.mark.parametrize("value", [
    {"a": 1, "b": [1, 2], "c": {"d": "x", "e": [{"f": 1}, {"g": [1, {"h": 2}]}]}, "i": {}, "j": [], "k": None},
    {"wires": {"SCK": "pmod_jd.[4]", "L/R": "pmod_jd.[6]"}, "params": {"width": 30}, "design_bits": {"buttons": [0, 1, 2, None]}},
    {1: "GND", "5": "j1[0]", "note": "it's # not a comment: really", "list": [[1, 2], [3]], "text": "a\nb"},
])
def test_a_block_reads_back_as_the_value(value):
    assert yaml.safe_load("\n".join(yt.block(value))) == value
    assert yaml.safe_load("\n".join(yt.entry("root", value, 2)))["root"] == value


def test_every_configuration_file_is_block_style():
    """Rig files, boards, peripherals, capabilities, modules, kinds, devices,
    connectors, registries, schemas' entity table: no `{a: b}` anywhere."""
    files = sorted(glob.glob(os.path.join(ROOT, "config", "**", "*.yml"), recursive=True))
    files += sorted(glob.glob(os.path.join(ROOT, "designs", "*", "fileset.yml")))
    assert len(files) > 300
    bad = {}
    for path in files:
        with open(path, encoding="utf-8") as f:
            found = flow_mappings(f.read())
        if found:
            bad[os.path.relpath(path, ROOT)] = found[:3]
    assert bad == {}, "flow mappings in configuration files (write them as blocks):\n" + "\n".join(
        "{}: {}".format(k, v) for k, v in sorted(bad.items()))


def test_every_rig_file_is_what_the_writer_writes():
    """The setup writer is idempotent over the repository: reading a rig and
    writing it back changes nothing (the one exception is a hand comment on
    a line, which the writer does not keep and the file may carry)."""
    differ = {}
    for path in sorted(glob.glob(os.path.join(ROOT, "config", "setups", "*.yml"))):
        sid = os.path.basename(path)[:-4]
        with open(path, encoding="utf-8") as f:
            text = f.read()
        written = su.dump_setup(su.read_setup(sid))
        if written != text:
            wl, tl = written.split("\n"), text.split("\n")
            if len(wl) != len(tl) or any(w != l and uncommented(l) != w for w, l in zip(wl, tl)) \
                    or yaml.safe_load(text) != yaml.safe_load(written):
                differ[sid] = written
        assert not flow_mappings(written), sid
    assert differ == {}, "rig files the writer would change: {}".format(sorted(differ))

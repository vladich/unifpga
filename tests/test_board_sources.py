"""
tools/board_sources.py: a board's documents and the facts read from them live
in the board's file; verify() checks the facts against the banks; the drafter
keeps a header or part that carries a source; fetching records the digest in
the board file.
"""

import os
import sys

import pytest
import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from config import init as config_init             # noqa: E402
from tools import board_sources as bs, layout_draft as ld, setup as su   # noqa: E402


def test_a_board_carries_its_documents_and_the_facts_verify():
    board = config_init.read_board("de10_lite")
    docs = bs.documents(board)
    assert {"manual", "litex", "amaranth"} <= set(docs)
    assert docs["manual"]["kind"] == "manual" and docs["manual"]["sha256"] and docs["manual"]["bytes"] > 0
    facts = [h for h in board["headers"] if h.get("source")]
    assert {h["bank"] for h in facts} == {"gpio", "arduino_io"}
    assert facts[0]["source"]["doc"] in docs
    v = bs.verify(board)
    assert v["documents"] == [] and all(p == [] for p in v["headers"].values()), v
    assert board["banks"]["onboard_leds"]["source"] == {"doc": "manual", "where": "Table 3-5, p. 28"}


def test_the_drafter_keeps_a_header_read_from_a_document():
    drawn = su.read_drawn("de10_lite")["headers"]
    drafted = ld.draft("de10_lite")["headers"]
    fact = next(c for c in drawn if c["bank"] == "gpio")
    again = next(c for c in drafted if c["bank"] == "gpio")
    assert again["type"] == fact["type"] == "terasic_gpio_2x20" and again["pins"] == fact["pins"]
    assert again["source"] == fact["source"]
    assert "source: {doc: manual" in ld.emit_drawn(ld.draft("de10_lite"))


def _board_tree(tmp_path, monkeypatch, board_text):
    (tmp_path / "chips" / "maker").mkdir(parents=True)
    (tmp_path / "chips" / "maker" / "family.yml").write_text(
        "Family: {id: family, producer: maker, name: Family, description: '', default_toolchains: [], chips: [{id: X}]}\n")
    (tmp_path / "boards" / "maker" / "family").mkdir(parents=True)
    path = tmp_path / "boards" / "maker" / "family" / "one.yml"
    path.write_text(board_text)
    (tmp_path / "connectors.yml").write_text(open(os.path.join(REPO, "config", "connectors.yml")).read())
    monkeypatch.setattr(config_init, "dir_path", str(tmp_path))
    config_init.clear_cache()
    return path


BOARD = """Board:
  id: one
  name: One
  producer: maker
  chip: X
  documents:
  - id: manual
    title: The manual
    kind: manual
    url: https://example.invalid/manual.pdf
  banks:
    j1:
      pins: [A1, A2, A3, A4]
      source: {doc: manual, where: p. 3}
    leds:
      pins: [B1, B2]
      source: {doc: nowhere, where: p. 4}
  # ---- drawn: headers and parts; ./unifpga layout draft one regenerates from here on ----
  layout: {verified: false, generated: true}
  headers:
    - id: j1
      type: pmod_2x6
      label: J1
      bank: j1
      pins: {'1': 'j1[0]', '2': 'j1[1]', '3': 'j1[2]', '5': 'j1[3]'}
      source: {doc: manual, where: p. 3}
  parts: []
"""


def test_verify_names_what_disagrees(tmp_path, monkeypatch):
    _board_tree(tmp_path, monkeypatch, BOARD)
    v = bs.verify(config_init.read_board("one"))
    assert v["documents"] == ["manual: not fetched yet (no sha256)"]
    assert v["headers"]["j1"] == ["pin 5 is GND on pmod_2x6, not a signal"]
    assert v["parts"]["leds"] == ["bank leds: its source names none of the board's documents"]
    assert v["parts"].get("j1", []) == []


def test_record_fetch_writes_the_digest_into_the_board_file(tmp_path, monkeypatch):
    path = _board_tree(tmp_path, monkeypatch, BOARD)
    bs.record_fetch("one", "manual", {"sha256": "ab" * 32, "bytes": 12})
    text = path.read_text()
    assert "    sha256: " + "ab" * 32 in text and "    bytes: 12" in text and "    retrieved: '" in text
    doc = yaml.safe_load(text)["Board"]["documents"][0]
    assert doc["sha256"] == "ab" * 32 and doc["bytes"] == 12
    assert "source: {doc: manual, where: p. 3}" in text            # the rest of the file as it was
    with pytest.raises(bs.SourcesError, match="no document 'other'"):
        bs.record_fetch("one", "other", {"sha256": "cd" * 32, "bytes": 1})

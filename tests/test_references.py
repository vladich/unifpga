"""config/references.py: the one parser of `<id>[<constraint>]` references
(toolchains of a chip, programmers of a board), shared by the live readers
and the Git-bound catalogue passes."""

import os
from pathlib import Path
import runpy
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from config import init as config_init                  # noqa: E402
from config.references import parse_versioned_ref       # noqa: E402


def test_shared_versioned_reference_parser_preserves_legacy_error_type():
    assert parse_versioned_ref("tool[*]") == ("tool", "*")
    assert config_init.parse_versioned_ref("tool") == ("tool", None)
    for invalid in ("tool[]", "tool[*]extra", "tool[bad\n]", "tool[nested[x]]",
                    None, ["tool"]):
        with pytest.raises(ValueError, match="Malformed versioned reference"):
            parse_versioned_ref(invalid)
        with pytest.raises(config_init.ConfigError, match="Malformed versioned reference"):
            config_init.parse_versioned_ref(invalid)


def test_direct_config_script_can_import_shared_reference_parser(monkeypatch):
    config_dir = Path(__file__).resolve().parents[1] / "config"
    monkeypatch.syspath_prepend(str(config_dir))
    namespace = runpy.run_path(str(config_dir / "init.py"), run_name="config_direct_probe")
    assert namespace["parse_versioned_ref"]("tool[*]") == ("tool", "*")

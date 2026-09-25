"""Every design on every rig it fits: the build accepts it (strict codegen), and,
where yosys is installed, the elaborated top has no conflicting drivers and,
outside the design itself, no undriven nets or logic loops
(tools/lint_generated.py --yosys)."""

import json
import os
import shutil
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from config import init as config_init  # noqa: E402
from tools import codegen, studio  # noqa: E402


def _pairs():
    for rig_id in sorted(config_init.read_configurations()):
        fit = studio.design_fit(config_init.resolve_configuration(rig_id))
        for design, unmet in sorted(fit.items()):
            if not unmet:
                yield rig_id, design


def test_every_fitting_design_builds_strictly():
    refused = []
    n = 0
    for rig_id, design in _pairs():
        n += 1
        resolved = config_init.resolve_configuration(rig_id)
        try:
            codegen.emit_top_sv(resolved, strict=True,
                                design=os.path.join(REPO, "designs", design, "design_top.sv"))
        except codegen.CodegenError as e:
            refused.append("{}/{}: {}".format(rig_id, design, str(e).splitlines()[0]))
    assert n > 0
    assert not refused, "\n".join(refused)


@pytest.mark.skipif(not (shutil.which("yosys") and shutil.which("iverilog")),
                    reason="needs yosys and Icarus Verilog")
def test_every_fitting_design_has_one_driver_per_net(tmp_path):
    out = str(tmp_path / "lint")
    tool = os.path.join(REPO, "tools", "lint_generated.py")
    subprocess.run([sys.executable, tool, "generate", "--out", out, "--design", "all"], check=True)
    run = subprocess.run([sys.executable, tool, "run", "--out", out, "--yosys"],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    with open(os.path.join(out, "results.json")) as f:
        results = json.load(f)["results"]
    assert results
    # FAIL is the wiring's (the generated top, the drivers); a design's own
    # findings (DESIGN) or sources this yosys cannot read (UNCHECKED) are not
    failed = ["{}: {}".format(r["id"], r["errors"][:1]) for r in results if r["status"] == "FAIL"]
    assert not failed, run.stdout[-4000:]

"""Every design's testbench (designs/*/tb.sv) compiles and runs through the
simulation flow (./unifpga sim): exit status 0 and no ERROR line (a $readmemh
that finds no data file reports one). Checks a testbench makes of an exercise
the student has not done yet (FAIL / mismatch lines) are the exercise's, not
the flow's. Skipped where Icarus Verilog is not installed."""

import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESIGNS = sorted(d for d in os.listdir(os.path.join(REPO, "designs"))
                 if os.path.isfile(os.path.join(REPO, "designs", d, "tb.sv")))


@pytest.mark.skipif(not (shutil.which("iverilog") and shutil.which("vvp")), reason="needs Icarus Verilog")
def test_every_testbench_runs():
    def run(design):
        proc = subprocess.run([sys.executable, os.path.join(REPO, "unifpga"), "sim", "--no-wave",
                               os.path.join("designs", design)],
                              cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              universal_newlines=True, timeout=900)
        errors = [l for l in proc.stdout.splitlines() if "ERROR" in l]
        return design, proc.returncode, errors

    with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
        results = list(pool.map(run, DESIGNS))
    bad = ["{}: exit {} {}".format(d, rc, errors[:1]) for d, rc, errors in results if rc or errors]
    assert len(results) == len(DESIGNS) > 80
    assert not bad, bad

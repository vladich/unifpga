"""rtl/peripherals/sd_spi_reader.sv against its self-checking Icarus testbench
(tests/sim/sd_spi_reader_tb.sv); skipped where Icarus Verilog is not installed."""

import os
import shutil
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.skipif(not (shutil.which("iverilog") and shutil.which("vvp")), reason="needs Icarus Verilog")
def test_sd_spi_reader_testbench(tmp_path):
    out = str(tmp_path / "tb")
    subprocess.run(["iverilog", "-g2012", "-o", out,
                    os.path.join(REPO, "tests", "sim", "sd_spi_reader_tb.sv"),
                    os.path.join(REPO, "rtl", "peripherals", "sd_spi_reader.sv")], check=True)
    run = subprocess.run(["vvp", "-n", out], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         universal_newlines=True, timeout=300)
    assert "PASS sd_spi_reader" in run.stdout and "FAIL" not in run.stdout, run.stdout

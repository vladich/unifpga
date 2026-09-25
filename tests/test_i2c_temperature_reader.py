"""rtl/peripherals/i2c_temperature_reader.sv against its self-checking Icarus testbench
(tests/sim/i2c_temperature_reader_tb.sv); skipped where Icarus Verilog is not installed."""

import os
import shutil
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.skipif(not (shutil.which("iverilog") and shutil.which("vvp")), reason="needs Icarus Verilog")
def test_i2c_temperature_reader_testbench(tmp_path):
    out = str(tmp_path / "tb")
    subprocess.run(["iverilog", "-g2012", "-o", out,
                    os.path.join(REPO, "tests", "sim", "i2c_temperature_reader_tb.sv"),
                    os.path.join(REPO, "rtl", "peripherals", "i2c_temperature_reader.sv"),
                    os.path.join(REPO, "rtl", "peripherals", "i2c_reg_master.sv")], check=True)
    run = subprocess.run(["vvp", "-n", out], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         universal_newlines=True, timeout=300)
    assert "PASS i2c_temperature_reader" in run.stdout and "FAIL" not in run.stdout, run.stdout

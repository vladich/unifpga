"""APS CPU, timer, LED and UART programmer through the public sim command."""

import os
import shutil
import subprocess
import sys

import pytest


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.skipif(not (shutil.which("iverilog") and shutil.which("vvp")),
                    reason="needs Icarus Verilog")
def test_aps_preloaded_firmware_runs_after_uart_programmer_release(tmp_path):
    command = [sys.executable, os.path.join(REPO, "unifpga"), "sim",
               "designs/5_5_aps", "--tb-top", "tb_firmware",
               "--output-dir", str(tmp_path / "sim"), "--no-wave"]
    result = subprocess.run(command, cwd=REPO, text=True, capture_output=True,
                            timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "APS_FIRMWARE_PASS timer reads and two firmware LED writes" in result.stdout

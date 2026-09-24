"""
tools/toolchain_detect.py against fake directory trees: one case per
search rule (pin, vendor variable, PATH, default parents, newest version,
edition selection, open-flow binary sets).
"""

import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from tools import toolchain_detect as td   # noqa: E402


class FakeFS(object):
    """A tree of executables / files; every parent directory exists."""

    def __init__(self, exes=(), files=()):
        self.exes = set(exes)
        self.files = set(files) | self.exes
        self.dirs = set()
        for f in self.files:
            d = os.path.dirname(f)
            while d and d != "/":
                self.dirs.add(d)
                d = os.path.dirname(d)
        self.dirs.add("/")

    def isdir(self, p):
        return p.rstrip("/") in self.dirs or p == "/"

    def isfile(self, p):
        return p in self.files

    def isexe(self, p):
        return p in self.exes

    def listdir(self, p):
        p = p.rstrip("/")
        out = set()
        for f in self.files | self.dirs:
            if os.path.dirname(f) == p and f != p:
                out.add(os.path.basename(f))
        return sorted(out)

    def which(self, name, path):
        for d in (path or "").split(os.pathsep):
            if d and os.path.join(d, name) in self.exes:
                return os.path.join(d, name)
        return None

    def realpath(self, p):
        return p


HOME = "/home/u"


def _detect(tid, fs, env=None, pin=None, system="linux"):
    env = dict(env or {})
    env.setdefault("PATH", "/usr/bin")
    return td.detect(tid, pin=pin, env=env, home=HOME, system=system, fs=fs)


# ---------------------------------------------------------------- Vivado

def test_vivado_pin_wins():
    fs = FakeFS(exes=["/x/Vivado/2022.2/bin/vivado", HOME + "/Xilinx/Vivado/2023.2/bin/vivado"])
    d = _detect("vivado", fs, pin="/x/Vivado/2022.2")
    assert d.found and d.source == "pin" and d.install_dir == "/x/Vivado/2022.2"


def test_vivado_env_then_path_then_search_newest():
    fs = FakeFS(exes=["/tools/Xilinx/Vivado/2019.2/bin/vivado", "/tools/Xilinx/Vivado/2023.2/bin/vivado",
                      "/opt/bin/vivado"])
    d = _detect("vivado", fs, env={"XILINX_VIVADO": "/tools/Xilinx/Vivado/2019.2"})
    assert d.source == "env:XILINX_VIVADO" and d.version == "2019.2"
    d = _detect("vivado", fs, env={"PATH": "/opt/bin"})
    assert d.source == "path" and d.install_dir == "/opt"
    d = _detect("vivado", fs)
    assert d.found and d.install_dir == "/tools/Xilinx/Vivado/2023.2" and d.source == "search:/tools/Xilinx/Vivado"
    assert any("multiple" in n for n in d.notes)
    assert d.bin_dirs == ["/tools/Xilinx/Vivado/2023.2/bin"]


def test_vivado_bad_pin_is_noted_and_search_continues():
    fs = FakeFS(exes=[HOME + "/Xilinx/Vivado/2023.2/bin/vivado"])
    d = _detect("vivado", fs, pin="~/Xilinx/Vivado/2099.1")
    assert d.found and d.source.startswith("search:") and any("pin" in n for n in d.notes)


def test_vivado_missing_lists_parents():
    d = _detect("vivado", FakeFS())
    assert not d.found and "/opt" in d.notes[-1] and HOME in d.notes[-1]


# ---------------------------------------------------------------- Quartus

def test_quartus_lite_newest_under_home():
    fs = FakeFS(exes=[HOME + "/intelFPGA_lite/20.1/quartus/bin/quartus_sh",
                      HOME + "/intelFPGA_lite/23.1std/quartus/bin/quartus_sh"])
    d = _detect("quartus_prime_lite", fs)
    assert d.found and d.install_dir == HOME + "/intelFPGA_lite/23.1std/quartus" and d.version == "23.1std"
    assert d.bins["quartus_sh"].endswith("/bin/quartus_sh")


def test_quartus2_uses_altera_and_rootdir():
    fs = FakeFS(exes=[HOME + "/altera/13.0sp1/quartus/bin/quartus_sh"])
    assert _detect("quartus2", fs).install_dir == HOME + "/altera/13.0sp1/quartus"
    assert not _detect("quartus_prime_lite", fs).found       # lite never looks in altera/
    d = _detect("quartus2", fs, env={"QUARTUS_ROOTDIR": HOME + "/altera/13.0sp1/quartus"})
    assert d.source == "env:QUARTUS_ROOTDIR"


def test_quartus_path_edition_check():
    """quartus_sh from a Lite install on PATH must not satisfy Pro/Standard
    (mercury: ~/intelFPGA_lite/20.1 on vladimir's PATH)."""
    fs = FakeFS(exes=[HOME + "/intelFPGA_lite/20.1/quartus/bin/quartus_sh"])
    env = {"PATH": HOME + "/intelFPGA_lite/20.1/quartus/bin"}
    assert _detect("quartus_prime_lite", fs, env=env).source == "path"
    d = _detect("quartus_prime_pro", fs, env=env)
    assert not d.found and "intelFPGA_lite install" in d.notes[0]
    # a custom layout on PATH is accepted (edition unknown)
    fs2 = FakeFS(exes=["/srv/q/quartus/bin/quartus_sh"])
    assert _detect("quartus_prime_standard", fs2, env={"PATH": "/srv/q/quartus/bin"}).found


def test_quartus_windows_bin64():
    fs = FakeFS(exes=["/c/intelFPGA_lite/22.1std/quartus/bin64/quartus_sh.exe"])
    d = _detect("quartus_prime_lite", fs, system="windows")
    assert d.found and d.bin_dirs == ["/c/intelFPGA_lite/22.1std/quartus/bin64"]


# ---------------------------------------------------------------- Gowin

def test_gowin_editions_from_mercury_layout():
    fs = FakeFS(exes=[HOME + "/Gowin/1.9.11.03.Educational/IDE/bin/gw_sh",
                      HOME + "/Gowin/1.9.11.03.Educational/Programmer/bin/programmer_cli",
                      HOME + "/Gowin/1.9.9.02/IDE/bin/gw_sh"])
    edu = _detect("gowin_eda", fs)
    std = _detect("gowin_standard", fs)
    assert edu.install_dir == HOME + "/Gowin/1.9.11.03.Educational" and "programmer_cli" in edu.bins
    assert std.install_dir == HOME + "/Gowin/1.9.9.02"
    assert edu.bin_dirs[0] == HOME + "/Gowin/1.9.11.03.Educational/IDE/bin"


def test_gowin_standard_refuses_educational_only():
    fs = FakeFS(exes=[HOME + "/Gowin/1.9.11.03.Educational/IDE/bin/gw_sh"])
    assert _detect("gowin_eda", fs).found
    d = _detect("gowin_standard", fs)
    assert not d.found and "Standard" in d.notes[-1]


def test_gowin_env_and_versioned_install_dirs():
    fs = FakeFS(exes=["/opt/gowin/Gowin_V1.9.9Beta/IDE/bin/gw_sh", "/opt/gowin/Gowin_V1.9.10/IDE/bin/gw_sh"])
    d = _detect("gowin_eda", fs)
    assert d.install_dir == "/opt/gowin/Gowin_V1.9.10"
    d = _detect("gowin_eda", fs, env={"GOWIN_VERSION_DIR": "/opt/gowin/Gowin_V1.9.9Beta"})
    assert d.source == "env:GOWIN_VERSION_DIR"


def test_gowin_macos_app_bundle():
    fs = FakeFS(exes=["/Applications/GowinIDE.app/Contents/Resources/Gowin_EDA/IDE/bin/gw_sh"])
    d = _detect("gowin_eda", fs, system="darwin")
    assert d.found and d.install_dir == "/Applications/GowinIDE.app/Contents/Resources/Gowin_EDA"


# ---------------------------------------------------------------- Efinity / Libero

def test_efinity_search_and_env():
    fs = FakeFS(files=[HOME + "/efinity/2023.2/scripts/efx_run.py", HOME + "/efinity/2022.1/scripts/efx_run.py"])
    d = _detect("efinity", fs)
    assert d.install_dir == HOME + "/efinity/2023.2"
    assert _detect("efinity", fs, env={"EFINITY_HOME": HOME + "/efinity/2022.1"}).version == "2022.1"


def test_libero_default_location():
    fs = FakeFS(exes=["/usr/local/microchip/Libero_SoC_v2024.1/Libero/bin/libero"])
    d = _detect("libero_soc", fs)
    assert d.found and d.install_dir == "/usr/local/microchip/Libero_SoC_v2024.1/Libero" and d.version == "2024.1"


# ---------------------------------------------------------------- open flows

def test_oss_cad_suite_required_binaries():
    root = HOME + "/oss-cad-suite/bin/"
    fs = FakeFS(exes=[root + "yosys", root + "nextpnr-ice40", root + "icepack", root + "nextpnr-ecp5"])
    d = _detect("nextpnr_icestorm", fs)
    assert d.found and d.source == "oss-cad-suite:" + root.rstrip("/") and d.bin_dirs == [root.rstrip("/")]
    d = _detect("nextpnr_trellis", fs)
    assert not d.found and "ecppack" in d.notes[0]


def test_oss_extra_build_dirs_and_alternatives():
    fs = FakeFS(exes=["/usr/bin/yosys", HOME + "/Projects/nextpnr/build/nextpnr-mistral",
                      HOME + "/Projects/nextpnr/build/nextpnr-himbaechel", HOME + "/Projects/prjpeppercorn/libgm/build/gmpack",
                      "/usr/bin/nextpnr-gowin", "/usr/bin/gowin_pack"])
    d = _detect("nextpnr_mistral", fs)
    assert d.found and d.bins["nextpnr-mistral"].startswith(HOME + "/Projects")
    assert _detect("nextpnr_gatemate", fs).found
    d = _detect("nextpnr_apicula", fs)
    assert d.found and d.bins["nextpnr-himbaechel"].endswith("nextpnr-himbaechel")   # alternative slot keeps the first name


def test_unknown_toolchain_has_a_reason():
    d = _detect("pango_ds", FakeFS())
    assert not d.found and "no detection rule" in d.notes[0]


def test_resolver_fills_install_dir_from_detection(monkeypatch):
    from config import init as config_init
    fs = FakeFS(exes=["/tools/Xilinx/Vivado/2023.2/bin/vivado"])
    monkeypatch.setattr(td, "detect", lambda tid, pin=None: td._detect_vivado(
        tid, None, {"PATH": ""}, HOME, "linux", fs))
    tc = config_init.resolve_toolchain_install({"Id": "vivado", "InstallDir": "~/nowhere"})
    assert tc["InstallDir"] == "/tools/Xilinx/Vivado/2023.2"
    assert tc["BinDirs"] == ["/tools/Xilinx/Vivado/2023.2/bin"] and tc["DetectSource"].startswith("search:")

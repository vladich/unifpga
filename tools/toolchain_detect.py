#!/usr/bin/env python3
"""
Toolchain auto-detection, the vendors' install locations searched in one
place:

    1. the `InstallDir` pin from config/toolchains.yml when it exists
       (`~` and `$VAR` expanded);
    2. the vendor's environment variable (XILINX_VIVADO, QUARTUS_ROOTDIR,
       GOWIN_VERSION_DIR, EFINITY_HOME, OSS_CAD_SUITE, ...);
    3. the tool already on PATH;
    4. the default install search: home-style parents (XILINX_HOME / INTEL_FPGA_HOME /
       ALTERA_HOME / QUARTUS_HOME / GOWIN_HOME, then $HOME, /opt, /tools on
       Linux; /Applications and ~/Applications too on macOS; /c, /d, /e on
       Windows) + the vendor's directory layout, newest version wins, a note
       when several versions are installed.

`detect(toolchain_id, pin=...)` returns a Detection (found or not, with the
reasons). Everything that touches the machine goes through the `fs`, `env`,
`home` and `system` arguments so the rules are unit-testable on a fake tree
(tests/test_toolchain_detect.py).

    python3 tools/toolchain_detect.py            # report for every toolchain id
"""

import os
import platform
import re
import shutil
import sys
from collections import namedtuple

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

Detection = namedtuple("Detection", "toolchain_id found install_dir bin_dirs bins source version notes")


# ---------------------------------------------------------------------------
# Machine access (swappable for tests)
# ---------------------------------------------------------------------------

class RealFS(object):
    def isdir(self, p):
        return os.path.isdir(p)

    def isfile(self, p):
        return os.path.isfile(p)

    def isexe(self, p):
        return os.path.isfile(p) and os.access(p, os.X_OK)

    def listdir(self, p):
        try:
            return sorted(os.listdir(p))
        except OSError:
            return []

    def which(self, name, path):
        return shutil.which(name, path=path)

    def realpath(self, p):
        return os.path.realpath(p)


def _system(name=None):
    if name:
        return name
    s = platform.system().lower()
    if s.startswith("darwin"):
        return "darwin"
    if s.startswith(("windows", "cygwin", "msys")) or os.name == "nt":
        return "windows"
    return "linux"


def _expand(p, env, home):
    if not p:
        return None
    p = str(p)
    if p.startswith("~"):
        p = home + p[1:]
    p = re.sub(r"\$(\w+)|\$\{(\w+)\}", lambda m: env.get(m.group(1) or m.group(2), ""), p)
    return p.rstrip("/") or "/"


def _vkey(name):
    """Natural version order: 1.9.11.03 > 1.9.9.02, 23.1std > 20.1, 2023.2 > 2019.2."""
    return tuple((0, int(t)) if t.isdigit() else (1, t.lower()) for t in re.findall(r"\d+|[A-Za-z]+", name))


def matches_version(version, constraint):
    """Match a catalogue constraint: *, exact, minimum+, or inclusive range.

    Numeric bounds use the same natural ordering as installation selection.
    Non-numeric identities (for example git-master) may only match exactly.
    """
    if constraint == "*":
        return True
    if not isinstance(constraint, str) or not constraint:
        raise ValueError("empty or non-string version constraint")
    numeric = r"\d[0-9A-Za-z.]*"
    if constraint.endswith("+"):
        bound = constraint[:-1]
        if not re.fullmatch(numeric, bound):
            raise ValueError("invalid minimum version constraint {!r}".format(constraint))
        return bool(version) and bool(re.match(r"^\d", str(version))) and _vkey(str(version)) >= _vkey(bound)
    bounds = re.fullmatch(r"({0})-({0})".format(numeric), constraint)
    if bounds:
        low, high = bounds.groups()
        if _vkey(low) > _vkey(high):
            raise ValueError("reversed version range {!r}".format(constraint))
        return bool(version) and bool(re.match(r"^\d", str(version))) and _vkey(low) <= _vkey(str(version)) <= _vkey(high)
    if "+" in constraint or "[" in constraint or "]" in constraint:
        raise ValueError("invalid version constraint {!r}".format(constraint))
    return bool(version) and str(version).casefold() == constraint.casefold()


def _parents(system, env, home, extra_env=()):
    out = []
    for var in extra_env:
        v = env.get(var)
        if v:
            out.append(_expand(v, env, home))
    if system == "windows":
        out += ["/c", "/d", "/e", "C:/", "D:/", "E:/"]
    else:
        if system == "darwin":
            out += ["/Applications", home + "/Applications"]
        out += [home, "/opt", "/tools"]
    seen, uniq = set(), []
    for p in out:
        if p and p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def _newest(cands, notes, what):
    """Pick the newest of [(version_name, path)]; note when there are several."""
    if not cands:
        return None
    cands = sorted(cands, key=lambda c: _vkey(c[0]))
    if len(cands) > 1:
        notes.append("multiple {} installed: {}; using the newest ({})".format(
            what, ", ".join(c[1] for c in cands), cands[-1][1]))
    return cands[-1]


def _found(tid, install_dir, bin_dirs, bins, source, version, notes):
    return Detection(tid, True, install_dir, list(bin_dirs), dict(bins), source, version, list(notes))


def _missing(tid, notes):
    return Detection(tid, False, None, [], {}, None, None, list(notes))


def _bin_dir_of(fs, path):
    return os.path.dirname(fs.realpath(path))


# ---------------------------------------------------------------------------
# Vendor rules
# ---------------------------------------------------------------------------

def _detect_vivado(tid, pin, env, home, system, fs):
    notes = []
    exe = "vivado.bat" if system == "windows" else "vivado"

    def ok(d):
        return fs.isexe(os.path.join(d, "bin", exe))

    def result(d, source):
        return _found(tid, d, [os.path.join(d, "bin")], {"vivado": os.path.join(d, "bin", exe)},
                      source, os.path.basename(d), notes)

    if pin and ok(pin):
        return result(pin, "pin")
    if pin:
        notes.append("InstallDir pin {!r} has no bin/vivado".format(pin))
    x = env.get("XILINX_VIVADO")
    if x:
        x = _expand(x, env, home)
        if ok(x):
            return result(x, "env:XILINX_VIVADO")
        notes.append("XILINX_VIVADO={!r} has no bin/vivado".format(x))
    w = fs.which(exe, env.get("PATH", ""))
    if w:
        d = os.path.dirname(_bin_dir_of(fs, w))
        return result(d, "path")
    for parent in _parents(system, env, home, ("XILINX_HOME",)):
        root = os.path.join(parent, "Xilinx", "Vivado")
        if not fs.isdir(root):
            continue
        cands = [(v, os.path.join(root, v)) for v in fs.listdir(root) if ok(os.path.join(root, v))]
        pick = _newest(cands, notes, "Vivado versions under " + root)
        if pick:
            return result(pick[1], "search:" + root)
    notes.append("no Xilinx/Vivado/<version>/bin/vivado under " + ", ".join(_parents(system, env, home, ("XILINX_HOME",))))
    return _missing(tid, notes)


def _detect_ise(tid, pin, env, home, system, fs):
    notes = []
    sub = os.path.join("bin", "nt64" if system == "windows" else "lin64")

    def ok(d):
        return fs.isexe(os.path.join(d, sub, "xst")) or fs.isexe(os.path.join(d, sub, "ise"))

    def result(d, source):
        return _found(tid, d, [os.path.join(d, sub)], {"xst": os.path.join(d, sub, "xst")},
                      source, os.path.basename(os.path.dirname(os.path.dirname(d))) or None, notes)

    if pin and ok(pin):
        return result(pin, "pin")
    x = env.get("XILINX")
    if x and ok(_expand(x, env, home)):
        return result(_expand(x, env, home), "env:XILINX")
    w = fs.which("xst", env.get("PATH", ""))
    if w:
        return result(os.path.dirname(os.path.dirname(_bin_dir_of(fs, w))), "path")
    for parent in _parents(system, env, home, ("XILINX_HOME",)):
        root = os.path.join(parent, "Xilinx")
        if not fs.isdir(root):
            continue
        cands = []
        for v in fs.listdir(root):
            d = os.path.join(root, v, "ISE_DS", "ISE")
            if ok(d):
                cands.append((v, d))
        pick = _newest(cands, notes, "ISE versions under " + root)
        if pick:
            return result(pick[1], "search:" + root)
    notes.append("no Xilinx/<version>/ISE_DS/ISE/{} under the search parents".format(sub))
    return _missing(tid, notes)


def _detect_quartus(tid, pin, env, home, system, fs, dirs):
    notes = []
    old = "altera" in dirs
    bindir = "bin64" if (system == "windows" and not old) else "bin"
    exe = ".exe" if system == "windows" else ""

    def ok(q):
        return os.path.basename(q) == "quartus" and fs.isexe(os.path.join(q, bindir, "quartus_sh" + exe))

    def result(q, source):
        b = os.path.join(q, bindir)
        return _found(tid, q, [b], {n: os.path.join(b, n + exe) for n in ("quartus_sh", "quartus_map", "quartus_pgm")},
                      source, os.path.basename(os.path.dirname(q)), notes)

    if pin and ok(pin):
        return result(pin, "pin")
    if pin:
        notes.append("InstallDir pin {!r} is not a quartus/ dir with {}/quartus_sh".format(pin, bindir))
    q = env.get("QUARTUS_ROOTDIR")
    if q:
        q = _expand(q, env, home)
        if ok(q):
            return result(q, "env:QUARTUS_ROOTDIR")
        notes.append("QUARTUS_ROOTDIR={!r} is not a quartus/ dir with {}/quartus_sh".format(q, bindir))
    w = fs.which("quartus_sh" + exe, env.get("PATH", ""))
    if w:
        q = os.path.dirname(_bin_dir_of(fs, w))
        family = os.path.basename(os.path.dirname(os.path.dirname(q)))
        known = ("intelFPGA_lite", "intelFPGA", "intelFPGA_pro", "altera")
        if ok(q) and (family in dirs or family not in known):
            return result(q, "path")
        if ok(q):
            notes.append("quartus_sh on PATH ({}) is a {} install, not one of {}".format(w, family, "/".join(dirs)))
        else:
            notes.append("quartus_sh on PATH ({}) is outside the usual quartus/{} layout".format(w, bindir))
    extra = () if old else ("INTEL_FPGA_HOME",)
    for parent in _parents(system, env, home, extra + ("ALTERA_HOME", "QUARTUS_HOME")):
        for sub in dirs:
            root = os.path.join(parent, sub)
            if not fs.isdir(root):
                continue
            cands = [(v, os.path.join(root, v, "quartus")) for v in fs.listdir(root)
                     if ok(os.path.join(root, v, "quartus"))]
            pick = _newest(cands, notes, "Quartus versions under " + root)
            if pick:
                return result(pick[1], "search:" + root)
    notes.append("no {}/<version>/quartus/{}/quartus_sh under {}".format(
        " or ".join(dirs), bindir, ", ".join(_parents(system, env, home, extra + ("ALTERA_HOME", "QUARTUS_HOME")))))
    return _missing(tid, notes)


_MAC_GOWIN_SUB = os.path.join("Contents", "Resources", "Gowin_EDA")


def _detect_gowin(tid, pin, env, home, system, fs, edition):
    notes = []
    exe = ".exe" if system == "windows" else ""

    def gw_sh(d):
        return os.path.join(d, "IDE", "bin", "gw_sh" + exe)

    def ok(d):
        return fs.isexe(gw_sh(d))

    def is_edu(d):
        return re.search(r"edu", d, re.I) is not None

    def result(d, source):
        bins = {"gw_sh": gw_sh(d)}
        bin_dirs = [os.path.join(d, "IDE", "bin")]
        prog = os.path.join(d, "Programmer", "bin", "programmer_cli" + exe)
        if fs.isexe(prog):
            bins["programmer_cli"] = prog
            bin_dirs.append(os.path.dirname(prog))
        ofl = fs.which("openFPGALoader", env.get("PATH", ""))
        if ofl:
            bins["openFPGALoader"] = ofl
        if edition == "standard" and is_edu(d):
            notes.append("{} is an Educational install; GW5* parts need the Standard edition".format(d))
        return _found(tid, d, bin_dirs, bins, source, os.path.basename(d), notes)

    def resolve_mac(d):
        return os.path.join(d, _MAC_GOWIN_SUB) if system == "darwin" and ok(os.path.join(d, _MAC_GOWIN_SUB)) else d

    if pin and ok(resolve_mac(pin)):
        return result(resolve_mac(pin), "pin")
    if pin:
        notes.append("InstallDir pin {!r} has no IDE/bin/gw_sh".format(pin))
    v = env.get("GOWIN_VERSION_DIR")
    if v:
        v = resolve_mac(_expand(v, env, home))
        if ok(v):
            return result(v, "env:GOWIN_VERSION_DIR")
        notes.append("GOWIN_VERSION_DIR={!r} has no IDE/bin/gw_sh".format(v))
    w = fs.which("gw_sh" + exe, env.get("PATH", ""))
    if w:
        d = os.path.dirname(os.path.dirname(_bin_dir_of(fs, w)))
        if ok(d):
            return result(d, "path")

    # Default search: <parent>/{Gowin,gowin[,GowinIDE.app]}/<version>/IDE/bin/gw_sh,
    # or the Gowin dir itself; every version dir that has gw_sh counts (a
    # `Gowin_*` name filter would miss mercury's
    # `~/Gowin/1.9.11.03.Educational`).
    subs = ["Gowin"] if system == "windows" else (["GowinIDE.app", "Gowin", "gowin"] if system == "darwin"
                                                 else ["Gowin", "gowin"])
    cands = []
    parents = _parents(system, env, home, ("GOWIN_HOME",))
    if system != "windows":
        parents.append(home + "/Downloads")
    for parent in parents:
        for sub in subs + [""]:
            d = os.path.join(parent, sub) if sub else parent
            if not fs.isdir(d):
                continue
            for v in fs.listdir(d):
                vd = resolve_mac(os.path.join(d, v))
                if ok(vd):
                    cands.append((v, vd))
            if sub and ok(resolve_mac(d)):
                cands.append((os.path.basename(d), resolve_mac(d)))
        if cands:
            break
    if not cands:
        notes.append("no Gowin/<version>/IDE/bin/gw_sh under " + ", ".join(parents))
        return _missing(tid, notes)
    if edition == "educational":
        edu = [c for c in cands if is_edu(c[1])]
        pool = edu or cands
    else:
        pool = [c for c in cands if not is_edu(c[1])]
        if not pool:
            notes.append("only Educational installs found ({}); the Standard edition (licence) is needed"
                         .format(", ".join(c[1] for c in cands)))
            return _missing(tid, notes)
    pick = _newest(pool, notes, "Gowin {} installs".format(edition))
    return result(pick[1], "search:" + os.path.dirname(pick[1]))


def _detect_efinity(tid, pin, env, home, system, fs):
    notes = []

    def ok(d):
        return fs.isfile(os.path.join(d, "scripts", "efx_run.py")) or fs.isfile(os.path.join(d, "bin", "setup.sh"))

    def result(d, source):
        return _found(tid, d, [os.path.join(d, "bin")],
                      {"efx_run.py": os.path.join(d, "scripts", "efx_run.py")}, source, os.path.basename(d), notes)

    if pin and ok(pin):
        return result(pin, "pin")
    e = env.get("EFINITY_HOME")
    if e:
        e = _expand(e, env, home)
        if ok(e):
            return result(e, "env:EFINITY_HOME")
        notes.append("EFINITY_HOME={!r} has no scripts/efx_run.py".format(e))
    for name in ("efx_run.py", "efinity_sh.sh"):
        w = fs.which(name, env.get("PATH", ""))
        if w:
            return result(os.path.dirname(_bin_dir_of(fs, w)), "path")
    parents = _parents(system, env, home)
    for parent in parents:
        root = os.path.join(parent, "efinity")
        if not fs.isdir(root):
            continue
        cands = [(v, os.path.join(root, v)) for v in fs.listdir(root) if ok(os.path.join(root, v))]
        pick = _newest(cands, notes, "Efinity versions under " + root)
        if pick:
            return result(pick[1], "search:" + root)
    notes.append("no efinity/<version>/scripts/efx_run.py under " + ", ".join(parents))
    return _missing(tid, notes)


def _detect_libero(tid, pin, env, home, system, fs):
    notes = []

    def ok(d):
        return fs.isexe(os.path.join(d, "bin", "libero"))

    def result(d, source):
        install_name = os.path.basename(os.path.dirname(d))
        version = install_name[len("Libero_SoC_v"):] if install_name.startswith("Libero_SoC_v") else install_name
        return _found(tid, d, [os.path.join(d, "bin")], {"libero": os.path.join(d, "bin", "libero")},
                      source, version, notes)

    if pin and ok(pin):
        return result(pin, "pin")
    for var in ("LIBERO_INSTALL_DIR", "LIBERO_HOME"):
        v = env.get(var)
        if v and ok(_expand(v, env, home)):
            return result(_expand(v, env, home), "env:" + var)
    w = fs.which("libero", env.get("PATH", ""))
    if w:
        return result(os.path.dirname(_bin_dir_of(fs, w)), "path")
    parents = ["/usr/local/microchip", "/opt/microchip", home + "/microchip"] + _parents(system, env, home)
    for parent in parents:
        if not fs.isdir(parent):
            continue
        cands = [(v, os.path.join(parent, v, "Libero")) for v in fs.listdir(parent)
                 if v.startswith("Libero_SoC") and ok(os.path.join(parent, v, "Libero"))]
        pick = _newest(cands, notes, "Libero versions under " + parent)
        if pick:
            return result(pick[1], "search:" + parent)
    notes.append("no Libero_SoC_v<version>/Libero/bin/libero under " + ", ".join(parents))
    return _missing(tid, notes)


# Open flows: which binaries each needs. `alternatives` accept any one.
_OSS_TOOLS = {
    "nextpnr_icestorm": (("yosys", "nextpnr-ice40", "icepack"), ("openFPGALoader", "iceprog")),
    "nextpnr_trellis":  (("yosys", "nextpnr-ecp5", "ecppack"), ("openFPGALoader",)),
    "nextpnr_nexus":    (("yosys", "nextpnr-nexus", "prjoxide"), ("openFPGALoader",)),
    "nextpnr_oxide":    (("yosys", "nextpnr-nexus", "prjoxide"), ("openFPGALoader",)),
    "nextpnr_apicula":  (("yosys", ("nextpnr-himbaechel", "nextpnr-gowin"), "gowin_pack"), ("openFPGALoader",)),
    "nextpnr_gatemate": (("yosys", "nextpnr-himbaechel", "gmpack"), ("openFPGALoader",)),
    "nextpnr_mistral":  (("yosys", "nextpnr-mistral"), ("openFPGALoader",)),
    "nextpnr_openxc7":  (("yosys", "nextpnr-xilinx", ("fasm2frames", "fasm2frames.py", "openxc7.fasm2frames"),
                          ("xc7frames2bit", "openxc7.xc7frames2bit")), ("openFPGALoader",)),
}


def _detect_oss(tid, pin, env, home, system, fs, required, optional):
    notes = []
    roots = []
    for cand in (env.get("OSS_CAD_SUITE"), pin, home + "/oss-cad-suite", home + "/Downloads/oss-cad-suite"):
        if not cand:
            continue
        cand = _expand(cand, env, home)
        for b in (cand, os.path.join(cand, "bin")):
            if fs.isdir(b) and b not in roots and (fs.isexe(os.path.join(b, "yosys")) or os.path.basename(b) == "bin"):
                roots.append(b)
    extra = [home + "/Projects/nextpnr/build", home + "/Projects/prjpeppercorn/libgm/build", "/snap/bin"]

    def find(name):
        for r in roots:
            p = os.path.join(r, name)
            if fs.isexe(p):
                return p, "oss-cad-suite"
        w = fs.which(name, env.get("PATH", ""))
        if w:
            return w, "path"
        for d in extra:
            p = os.path.join(d, name)
            if fs.isexe(p):
                return p, "extra"
        return None, None

    bins, sources, missing = {}, set(), []
    for item in required:
        names = item if isinstance(item, tuple) else (item,)
        for n in names:
            p, src = find(n)
            if p:
                bins[names[0]] = p
                sources.add(src)
                break
        else:
            missing.append("/".join(names))
    for n in optional:
        p, src = find(n)
        if p:
            bins[n] = p
    if missing:
        notes.append("missing {}; searched {} then PATH then {}".format(
            ", ".join(missing), ", ".join(roots) or "no oss-cad-suite", ", ".join(extra)))
        return _missing(tid, notes)
    bin_dirs = []
    for p in bins.values():
        d = os.path.dirname(p)
        if d not in bin_dirs:
            bin_dirs.append(d)
    source = "oss-cad-suite:" + roots[0] if "oss-cad-suite" in sources and roots else ("path" if "path" in sources else "extra")
    install = roots[0] if "oss-cad-suite" in sources and roots else None
    return _found(tid, install, bin_dirs, bins, source, None, notes)


def _rules():
    r = {
        "vivado": _detect_vivado,
        "ise": _detect_ise,
        "quartus_prime_lite": lambda *a: _detect_quartus(*a, dirs=("intelFPGA_lite", "intelFPGA")),
        "quartus_prime_standard": lambda *a: _detect_quartus(*a, dirs=("intelFPGA",)),
        "quartus_prime_pro": lambda *a: _detect_quartus(*a, dirs=("intelFPGA_pro",)),
        "quartus2": lambda *a: _detect_quartus(*a, dirs=("altera",)),
        "gowin_eda": lambda *a: _detect_gowin(*a, edition="educational"),
        "gowin_standard": lambda *a: _detect_gowin(*a, edition="standard"),
        "efinity": _detect_efinity,
        "libero_soc": _detect_libero,
    }
    for tid, (req, opt) in _OSS_TOOLS.items():
        r[tid] = (lambda req, opt: (lambda *a: _detect_oss(*a, required=req, optional=opt)))(req, opt)
    return r


RULES = _rules()

_CACHE = {}


def detect(toolchain_id, pin=None, env=None, home=None, system=None, fs=None):
    """Detection for one toolchain id. With the default (real) machine access
    the result is cached per (toolchain_id, pin) for the process."""
    real = env is None and fs is None and home is None and system is None
    key = (toolchain_id, pin)
    if real and key in _CACHE:
        return _CACHE[key]
    env = dict(os.environ) if env is None else env
    home = (home or env.get("HOME") or os.path.expanduser("~")).rstrip("/")
    system = _system(system)
    fs = fs or RealFS()
    rule = RULES.get(toolchain_id)
    if rule is None:
        det = _missing(toolchain_id, ["no detection rule for this toolchain; set InstallDir in config/toolchains.yml"])
    else:
        det = rule(toolchain_id, _expand(pin, env, home) if pin else None, env, home, system, fs)
    if real:
        _CACHE[key] = det
    return det


def report(toolchains):
    """Lines for `synthesize.py --list-toolchains`."""
    lines = []
    for tid, tc in sorted(toolchains.items()):
        det = detect(tid, pin=tc.get("InstallDir"))
        operations = ", ".join(tc.get("SupportedOperations") or []) or "catalogue only"
        if det.found:
            where = det.install_dir or ", ".join(det.bin_dirs)
            lines.append("{:24s} found   {} ({}{}); operations: {}".format(
                tid, where, det.source,
                ", version " + det.version if det.version else "", operations))
        else:
            lines.append("{:24s} missing {}; operations: {}".format(
                tid, "; ".join(det.notes), operations))
        for n in (det.notes if det.found else []):
            lines.append("{:24s}         note: {}".format("", n))
    return lines


if __name__ == "__main__":
    sys.path.insert(0, REPO)
    from config import init as config_init
    print("\n".join(report(config_init.read_toolchains())))

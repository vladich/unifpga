"""Quartus II 13.x allocator workaround (toolchains/quartus_prime)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from toolchains.quartus_prime import quartus_prime as qp  # noqa: E402

Q13 = {"Id": "quartus2", "Version": "13.0sp1"}


def _lib(tmp_path, name):
    p = tmp_path / name
    p.write_text("")
    return str(p)


def test_q13_on_linux_preloads_the_first_allocator_found(tmp_path):
    tc = _lib(tmp_path, "libtcmalloc_minimal.so.4")
    env = {"LD_PRELOAD": "/x/other.so"}
    assert qp._preload_allocator(env, Q13, lib_dirs=[str(tmp_path)], system="Linux") == tc
    assert env["LD_PRELOAD"] == tc + " /x/other.so"
    je = _lib(tmp_path, "libjemalloc.so.2")                     # jemalloc is preferred
    env = {}
    assert qp._preload_allocator(env, Q13, lib_dirs=[str(tmp_path)], system="Linux") == je
    assert env["LD_PRELOAD"] == je


def test_only_quartus_13_on_linux(tmp_path):
    _lib(tmp_path, "libjemalloc.so.2")
    for tc, system in (({"Id": "quartus_prime_lite", "Version": "23.1std"}, "Linux"), (Q13, "Darwin")):
        env = {}
        assert qp._preload_allocator(env, tc, lib_dirs=[str(tmp_path)], system=system) is None
        assert "LD_PRELOAD" not in env


def test_override_and_opt_out(tmp_path, monkeypatch):
    warned = []
    monkeypatch.setattr(qp.log, "warning", lambda msg, *a: warned.append(msg % a))
    mine = _lib(tmp_path, "custom_malloc.so")
    env = {"UNIFPGA_QUARTUS_MALLOC": mine}
    assert qp._preload_allocator(env, Q13, lib_dirs=[], system="Linux") == mine
    env = {"UNIFPGA_QUARTUS_MALLOC": "off"}
    assert qp._preload_allocator(env, Q13, lib_dirs=[str(tmp_path)], system="Linux") is None
    env = {}
    assert qp._preload_allocator(env, Q13, lib_dirs=[str(tmp_path / "none")], system="Linux") is None
    assert "LD_PRELOAD" not in env and "no standalone malloc" in warned[0]

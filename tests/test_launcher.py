"""The ./unifpga launcher brings the Python it needs: it runs with the
interpreter that has the requirements, else re-executes itself with the
repository's .venv (made with the requirements on first use), and never
installs into a Python of yours."""

import importlib.machinery
import importlib.util
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAUNCHER = os.path.join(ROOT, "unifpga")


def load_launcher():
    loader = importlib.machinery.SourceFileLoader("unifpga_launcher", LAUNCHER)
    spec = importlib.util.spec_from_loader("unifpga_launcher", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_the_launcher_names_the_requirements_by_import_name():
    mod = load_launcher()
    with open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8") as f:
        wanted = {line.split(">=")[0].split("==")[0].strip().lower() for line in f if line.strip() and not line.startswith("#")}
    assert wanted == {"pyyaml", "jsonschema"} and mod.MODULES == ("yaml", "jsonschema")
    assert mod.VENV == os.path.join(ROOT, ".venv") and mod.VENV_PYTHON.startswith(mod.VENV)


def test_an_interpreter_with_the_packages_is_used_as_it_is(monkeypatch):
    mod = load_launcher()
    monkeypatch.delenv("UNIFPGA_PYTHON", raising=False)
    assert mod.missing_here() == []                      # the test interpreter has them (tests import yaml)
    assert mod.missing_for(sys.executable) == []
    assert mod.python_to_use() is None
    assert mod.missing_for(os.path.join(ROOT, "no", "such", "python")) == ["yaml", "jsonschema"]


def test_a_named_interpreter_without_the_packages_is_refused_with_the_install_line(monkeypatch, tmp_path):
    mod = load_launcher()
    fake = tmp_path / "python"                           # runs, but has neither package
    fake.write_text("#!/bin/sh\nexec {} -S -c \"$@\"\n".format(sys.executable))   # -S: no site-packages
    fake.chmod(0o755)
    monkeypatch.setattr(mod, "missing_here", lambda: ["yaml"])
    monkeypatch.setenv("UNIFPGA_PYTHON", str(fake))
    with pytest.raises(SystemExit) as e:
        mod.python_to_use()
    assert "UNIFPGA_PYTHON={} lacks".format(fake) in str(e.value) and "-m pip install -r requirements.txt" in str(e.value)


def test_a_launcher_already_in_the_venv_does_not_hop_again(monkeypatch):
    mod = load_launcher()
    monkeypatch.setenv(mod.HOP, "1")
    monkeypatch.setattr(mod, "python_to_use", lambda: sys.executable)
    monkeypatch.setattr(mod, "missing_here", lambda: ["jsonschema"])
    with pytest.raises(SystemExit) as e:
        mod.main()
    assert "still lacks jsonschema" in str(e.value)


def test_the_launcher_runs_the_cli_from_a_python_without_the_packages(tmp_path):
    """A stand-in interpreter that lacks PyYAML runs ./unifpga: the launcher hands
    over to the .venv (or the interpreter UNIFPGA_PYTHON names) and the command
    works. Here UNIFPGA_PYTHON is this test's interpreter, which has them."""
    bare = tmp_path / "python3"
    bare.write_text("#!/bin/sh\nexec {} -S \"$@\"\n".format(sys.executable))
    bare.chmod(0o755)
    env = dict(os.environ, UNIFPGA_PYTHON=sys.executable)
    env.pop("UNIFPGA_BOOTSTRAP", None)
    probe = subprocess.run([str(bare), "-c", "import yaml"], capture_output=True)
    assert probe.returncode != 0, "the stand-in must lack PyYAML for this test to mean anything"
    out = subprocess.run([str(bare), LAUNCHER, "setup", "show", "arty_a7"], capture_output=True, text=True, env=env, cwd=ROOT, timeout=300)
    assert out.returncode == 0, out.stderr
    assert out.stdout.startswith("# Configuration 'arty_a7'.")

"""
tools/cli.py -- the short `./unifpga` command line, without any toolchain:
design resolution, the remembered board (settings.yml round trip,
$UNIFPGA_BOARD, -b), the exact synthesize.py argument list, clean's refusal
to delete anything but <design>/run/, and the launcher's help.
"""

import os
import subprocess
import sys

import pytest
import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import config.init                       # noqa: E402
import synthesize                        # noqa: E402
from tools import cli, toolchain_detect  # noqa: E402

ORIGINAL_SETTINGS_PATH = cli.SETTINGS_PATH   # before the autouse fixture redirects it
CFG = "tang_nano_9k_hdmi_tm1638"             # toolchain gowin_eda
DESIGN = "1_06_binary_counter"
DESIGN_DIR = os.path.join(cli.DESIGNS_DIR, DESIGN)
TOP = os.path.join(DESIGN_DIR, cli.TOP_NAME)


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Never touch the user's settings.yml or inherit UNIFPGA_BOARD."""
    path = tmp_path / "settings.yml"
    monkeypatch.setattr(cli, "SETTINGS_PATH", str(path))
    monkeypatch.delenv(cli.ENV_BOARD, raising=False)
    return path


@pytest.fixture
def gowin_only(monkeypatch):
    """Toolchain detection without touching the machine: only gowin_eda found."""
    def fake_detect(tid, pin=None, **kw):
        if tid == "gowin_eda":
            return toolchain_detect._found(tid, "/x/gowin", ["/x/gowin/IDE/bin"], {}, "stub", "1.9", [])
        return toolchain_detect._missing(tid, ["stubbed"])
    monkeypatch.setattr(toolchain_detect, "detect", fake_detect)


@pytest.fixture
def design(tmp_path):
    d = tmp_path / "my_design"
    d.mkdir()
    (d / cli.TOP_NAME).write_text("module design_top; endmodule\n")
    return d


@pytest.fixture
def captured(monkeypatch):
    """synthesize.main replaced by a recorder; calls[0] is the argv, rc[0] the return code."""
    calls, rc = [], [0]

    def fake_main(argv=None):
        calls.append(list(argv))
        return rc[0]
    monkeypatch.setattr(synthesize, "main", fake_main)
    return calls, rc


def _ids():
    return sorted(config.init.read_configurations())


# ---------------------------------------------------------------- designs

def test_resolve_design_from_cwd(design):
    got = cli.resolve_design(None, cwd=str(design))
    assert os.path.realpath(got) == os.path.realpath(str(design))


def test_resolve_design_by_name_and_path(tmp_path, design):
    assert cli.resolve_design(DESIGN, cwd=str(tmp_path)) == DESIGN_DIR
    assert cli.resolve_design(DESIGN + "/", cwd=str(tmp_path)) == DESIGN_DIR
    assert cli.resolve_design(os.path.join("designs", DESIGN), cwd=REPO) == DESIGN_DIR
    assert cli.resolve_design(TOP, cwd=str(tmp_path)) == DESIGN_DIR
    assert cli.resolve_design(str(design), cwd=REPO) == str(design)
    assert cli.resolve_design("my_design", cwd=str(tmp_path)) == str(design)


def test_resolve_design_missing_lists_candidates(tmp_path):
    with pytest.raises(cli.CliError) as e:
        cli.resolve_design("1_06_binary_countr", cwd=str(tmp_path))
    assert "Did you mean" in str(e.value) and DESIGN in str(e.value)

    with pytest.raises(cli.CliError) as e:          # nothing close: every design is listed
        cli.resolve_design("zzz_no_such_design", cwd=str(tmp_path))
    assert DESIGN in str(e.value) and "unifpga designs" in str(e.value)

    with pytest.raises(cli.CliError) as e:          # no argument, cwd is not a design
        cli.resolve_design(None, cwd=str(tmp_path))
    assert cli.TOP_NAME in str(e.value) and DESIGN in str(e.value)

    (tmp_path / "empty").mkdir()
    with pytest.raises(cli.CliError, match="has no design_top.sv"):
        cli.resolve_design("empty", cwd=str(tmp_path))


def test_designs_lists_directories_with_design_top(capsys):
    assert cli.main(["designs"]) == 0
    names = capsys.readouterr().out.split()
    assert DESIGN in names and names == sorted(names) and names == cli.list_designs()
    assert all(os.path.isfile(os.path.join(cli.DESIGNS_DIR, n, cli.TOP_NAME)) for n in names)


# ---------------------------------------------------------------- board

def test_settings_file_is_the_one_config_init_reads():
    # config/init.py read_all() opens os.path.join(dir_path, "..", "settings.yml").
    expected = os.path.normpath(os.path.join(config.init.dir_path, "..", "settings.yml"))
    assert os.path.normpath(ORIGINAL_SETTINGS_PATH) == expected


def test_board_round_trip(isolated_settings, gowin_only, capsys):
    assert cli.read_settings() is None
    assert cli.main(["board", CFG]) == 0
    # exactly the document config/init.py init() writes and read_all() reads
    assert yaml.safe_load(isolated_settings.read_text()) == {"ConfigurationId": CFG}
    assert cli.read_settings() == CFG
    assert cli.chosen_configuration() == CFG
    out = capsys.readouterr().out
    assert CFG in out and "gowin_eda" in out and "note:" not in out

    ids = _ids()
    other = [i for i in ids if i != CFG][0]
    assert cli.main(["board", other]) == 0           # overwrite
    assert cli.read_settings() == other
    assert "note: toolchain" in capsys.readouterr().out   # its toolchain is "missing" in the stub


def test_board_unknown_id(isolated_settings, gowin_only, capsys):
    assert cli.main(["board", "tang_nano_9k_hdmi_tm163"]) == 1
    assert not isolated_settings.exists()
    err = capsys.readouterr().err
    assert "board -l" in err and CFG in err          # did-you-mean


def test_board_list_does_not_prompt_and_marks_installed(isolated_settings, gowin_only, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("board -l must not prompt"))
    cli.write_settings(CFG)
    assert cli.main(["board", "--list"]) == 0
    out = capsys.readouterr().out
    ids = _ids()
    lines = {}
    for line in out.splitlines():
        for cfg_id in ids:
            if " {} ".format(cfg_id) in line:
                lines[cfg_id] = line
    assert set(lines) == set(ids)
    assert lines[CFG].startswith(">") and " * " in lines[CFG]              # current + gowin found
    vivado = [i for i in ids if config.init.read_configurations()[i]["toolchain"] == "vivado"][0]
    assert not lines[vivado].startswith(">") and " * " not in lines[vivado]
    assert "Current choice: " + CFG in out


def test_board_menu_reprompts_then_persists(isolated_settings, gowin_only, monkeypatch, capsys):
    ids = _ids()
    answers = iter(["999", "abc", "2"])
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    assert cli.main(["board"]) == 0
    assert cli.read_settings() == ids[1]
    assert capsys.readouterr().out.count("please choose again") == 2


def test_board_menu_accepts_an_id_and_gives_up_on_eof(isolated_settings, gowin_only, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda *a: CFG)
    assert cli.main(["board"]) == 0 and cli.read_settings() == CFG

    def eof(*a):
        raise EOFError
    monkeypatch.setattr("builtins.input", eof)
    assert cli.main(["board"]) == 0                  # a choice exists: kept
    assert cli.read_settings() == CFG
    isolated_settings.unlink()
    assert cli.main(["board"]) == 1                  # nothing chosen yet
    assert not isolated_settings.exists()


def test_env_and_flag_override(isolated_settings, monkeypatch):
    ids = _ids()
    cli.write_settings(ids[0])
    assert cli.chosen_configuration() == ids[0]
    monkeypatch.setenv(cli.ENV_BOARD, ids[1])
    assert cli.chosen_configuration() == ids[1]      # env beats settings.yml
    assert cli.chosen_configuration(ids[2]) == ids[2]  # -b beats env
    monkeypatch.setenv(cli.ENV_BOARD, "bogus")
    with pytest.raises(cli.CliError, match=cli.ENV_BOARD):
        cli.chosen_configuration()
    assert cli.read_settings() == ids[0]             # the file is untouched by overrides


def test_no_board_chosen_is_actionable(capsys):
    with pytest.raises(cli.CliError, match=r"\./unifpga board"):
        cli.chosen_configuration()
    assert cli.main(["build", DESIGN]) == 1
    assert "./unifpga board" in capsys.readouterr().err


def test_legacy_and_malformed_settings(isolated_settings):
    isolated_settings.write_text("BoardId: basys3\nToolchain: vivado\n")
    with pytest.raises(cli.CliError, match="legacy"):
        cli.read_settings()
    isolated_settings.write_text("")
    assert cli.read_settings() is None
    isolated_settings.write_text("- just\n- a list\n")
    with pytest.raises(cli.CliError, match="unifpga board"):
        cli.read_settings()


# ---------------------------------------------------------------- build / program

def test_build_assembles_synthesize_argv(captured, capsys):
    calls, rc = captured
    rc[0] = 7
    assert cli.main(["build", DESIGN, "-b", CFG]) == 7   # synthesize's return code
    assert calls == [["-c", CFG, "--top", TOP, "-s", "full",
                      "-o", os.path.join(DESIGN_DIR, "run", CFG)]]
    assert capsys.readouterr().out.startswith("Building {} for {}".format(DESIGN, CFG))


def test_build_from_design_directory_uses_remembered_board(design, captured, monkeypatch):
    calls, _ = captured
    cli.write_settings(CFG)
    monkeypatch.chdir(str(design))
    assert cli.main(["build", "-s", "elaborate"]) == 0
    argv = calls[0]
    assert argv[argv.index("-c") + 1] == CFG
    assert argv[argv.index("-s") + 1] == "elaborate"
    assert os.path.realpath(argv[argv.index("--top") + 1]) == os.path.realpath(str(design / cli.TOP_NAME))
    assert os.path.realpath(argv[argv.index("-o") + 1]) == os.path.realpath(str(design / "run" / CFG))
    assert "--program" not in argv


def test_program_adds_program_flag(captured, monkeypatch, capsys):
    calls, _ = captured
    monkeypatch.setenv(cli.ENV_BOARD, CFG)
    assert cli.main(["program", DESIGN]) == 0
    assert calls == [["-c", CFG, "--top", TOP, "-s", "full",
                      "-o", os.path.join(DESIGN_DIR, "run", CFG), "--program"]]
    assert "programming" in capsys.readouterr().out


def test_build_rejects_unknown_step(captured):
    with pytest.raises(SystemExit):
        cli.main(["build", DESIGN, "-b", CFG, "-s", "route"])
    assert captured[0] == []


# ---------------------------------------------------------------- clean

def test_clean_removes_only_run(design, capsys):
    run = design / "run" / CFG
    run.mkdir(parents=True)
    (run / "top.sv").write_text("x")
    (design / "keep.txt").write_text("k")
    assert cli.main(["clean", str(design)]) == 0
    assert not (design / "run").exists()
    assert (design / "keep.txt").exists() and (design / cli.TOP_NAME).exists()
    assert "Removed" in capsys.readouterr().out
    assert cli.main(["clean", str(design)]) == 0     # nothing left to do is not an error
    assert "Nothing to clean" in capsys.readouterr().out


def test_clean_refuses_outside_run(design, tmp_path, capsys):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious").write_text("p")
    (design / "run").symlink_to(outside)
    with pytest.raises(cli.CliError, match="symbolic link"):
        cli.remove_run_dir(str(design))
    assert cli.main(["clean", str(design)]) == 1
    assert (outside / "precious").exists() and (design / "run").is_symlink()
    (design / "run").unlink()

    (design / "run").write_text("a file, not a directory")
    with pytest.raises(cli.CliError, match="not a directory"):
        cli.remove_run_dir(str(design))
    assert (design / "run").is_file()

    other = tmp_path / "not_a_design"                # no design_top.sv: not ours to clean
    (other / "run").mkdir(parents=True)
    with pytest.raises(cli.CliError, match="not a design directory"):
        cli.remove_run_dir(str(other))
    assert (other / "run").is_dir()
    assert cli.main(["clean", str(other)]) == 1
    assert (other / "run").is_dir()


# ---------------------------------------------------------------- tools / launcher

def test_tools_reports_every_toolchain(gowin_only, capsys):
    assert cli.main(["tools"]) == 0
    out = capsys.readouterr().out
    for tid in config.init.read_toolchains():
        assert tid in out
    assert "gowin_eda" in out and "found" in out


def test_launcher_help_and_no_args_from_a_subprocess(tmp_path):
    launcher = os.path.join(REPO, "unifpga")
    assert os.access(launcher, os.X_OK)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    for argv in ([launcher, "-h"], [launcher]):
        r = subprocess.run([sys.executable] + argv, cwd=str(tmp_path), env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, timeout=120)
        assert r.returncode == 0, r.stderr
        assert "Quick start" in r.stdout and "./unifpga board" in r.stdout
        for cmd in cli.COMMANDS:
            assert cmd in r.stdout
    assert not (tmp_path / "settings.yml").exists()


# ---------------------------------------------------------------- BGM script parity

def test_sim_command_mirrors_bgm_run_icarus_verilog(tmp_path):
    d = tmp_path / "designs" / "x"
    d.mkdir(parents=True)
    (d / "design_top.sv").write_text("module design_top; endmodule\n")
    (d / "tb.sv").write_text("module tb; endmodule\n")
    cmd = cli.sim_command(str(d), str(tmp_path / "out"), "-g2012")
    assert cmd[:5] == ["iverilog", "-g2012", "-s", "tb", "-o"]
    assert "-I" in cmd and str(d) in cmd
    assert str(d / "tb.sv") in cmd and str(d / "design_top.sv") in cmd
    assert any(p.endswith(os.path.join("designs_common", "seven_segment_display.sv")) for p in cmd)


def test_iverilog_language_option_like_bgm():
    assert cli._iverilog_language_option("Icarus Verilog version 12.0 (stable)") == "-g2012"
    assert cli._iverilog_language_option("Icarus Verilog version 14.0 (devel)") == "-g2023"
    assert cli._iverilog_language_option("") == "-g2012"


def test_gui_command_table(tmp_path):
    out = str(tmp_path)
    cmd, why = cli.gui_command("quartus_prime_lite", out)
    assert cmd is None and "run build first" in why
    (tmp_path / "unifpga_top.qpf").write_text("")
    assert cli.gui_command("quartus_prime_lite", out)[0] == ["quartus", os.path.join(out, "unifpga_top.qpf")]
    assert cli.gui_command("vivado", out)[0] == ["vivado"]
    (tmp_path / "post_synth.dcp").write_text("")
    (tmp_path / "post_route.dcp").write_text("")
    assert cli.gui_command("vivado", out)[0] == ["vivado", os.path.join(out, "post_route.dcp")]
    assert cli.gui_command("gowin_eda", out)[0] is None
    assert cli.gui_command("nextpnr_icestorm", out)[0] is None


def test_clean_all_removes_every_design_run_dir(tmp_path, monkeypatch):
    designs = tmp_path / "designs"
    for name in ("a", "b"):
        (designs / name).mkdir(parents=True)
        (designs / name / "design_top.sv").write_text("")
        (designs / name / "run").mkdir()
    monkeypatch.setattr(cli, "DESIGNS_DIR", str(designs))
    assert cli.main(["clean", "--all"]) == 0
    assert not (designs / "a" / "run").exists() and not (designs / "b" / "run").exists()

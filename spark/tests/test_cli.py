from pathlib import Path

import pytest
import yaml

from spark import cli, launch

FIX = Path(__file__).parent / "fixtures"


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == "spark 0.1.0"


def test_no_command_prints_help(capsys):
    assert cli.main([]) == 0
    assert "usage: spark" in capsys.readouterr().out


# Task 6's fix round 1: a refusal is one line with its reason, never a traceback.

def test_a_refusal_says_why_in_one_line(tmp_path, capsys):
    # RegistryError, RenderError and VersionsError are ValueErrors whose message is the reason.
    data = yaml.safe_load((FIX / "models.yaml").read_text())
    data["models"]["coder"]["footprint_gib"] = 72
    registry = tmp_path / "models.yaml"
    registry.write_text(yaml.safe_dump(data))
    out = tmp_path / "out"
    code = cli.main(["render", "--out", str(out), "--registry", str(registry), "--versions", str(FIX / "versions.yaml")])
    assert code == 1
    assert capsys.readouterr().err == ("spark render: coder: needs 72.0 GiB, but 71.0 GiB is free for a load beside "
                                       "the always-loaded models with nothing else running, so the gate would refuse "
                                       "it even then (needs_room: true marks a model that loads only once make-room "
                                       "has freed room for it)\n")
    assert not out.exists()  # a refused render writes nothing


@pytest.mark.parametrize("bad", ["a missing registry", "a folder for a registry", "a registry that isn't YAML",
                                 "a registry that isn't UTF-8", "a registry that isn't valid",
                                 "a registry nested too deep", "a missing versions file", "versions that aren't YAML",
                                 "versions that aren't UTF-8"])
def test_a_file_that_cant_be_read_says_why_in_one_line(tmp_path, capsys, bad):
    # OSError (missing, a folder), yaml.YAMLError (not YAML) and a decoding error are refusals too, not tracebacks,
    # and each names its file, as launch does: YAML's message says only "<unicode string>", and a codec's names none.
    registry, versions = tmp_path / "models.yaml", tmp_path / "versions.yaml"
    registry.write_text((FIX / "models.yaml").read_text())
    versions.write_text((FIX / "versions.yaml").read_text())
    if bad == "a missing registry":
        registry.unlink()
    elif bad == "a folder for a registry":
        registry.unlink()
        registry.mkdir()
    elif bad == "a registry that isn't YAML":
        registry.write_text("budget: {allocatable_gib: 102\n  : [\n")
    elif bad == "a registry that isn't UTF-8":
        registry.write_bytes(b"budget: {allocatable_gib: \xff}\n")
    elif bad == "a registry that isn't valid":
        registry.write_text("budget: {allocatable_gib: 102}\n")
    elif bad == "a registry nested too deep":  # load_registry raises RecursionError
        registry.write_text("budget: " + "[" * 100_000 + "]" * 100_000 + "\n")
    elif bad == "a missing versions file":
        versions.unlink()
    elif bad == "versions that aren't YAML":
        versions.write_text("components: {llama-swap: [\n")
    else:
        versions.write_bytes(b"components: {llama-swap: \xff}\n")
    out = tmp_path / "out"
    code = cli.main(["render", "--out", str(out), "--registry", str(registry), "--versions", str(versions)])
    err = capsys.readouterr().err
    named = f"the registry {registry}" if "registry" in bad else f"the versions file {versions}"
    assert code == 1
    assert err.startswith(f"spark render: {named} won't load: ") and "Traceback" not in err, err
    assert not out.exists()  # a refused render writes nothing


def test_a_command_keeps_the_exit_code_it_chose(tmp_path, monkeypatch, capsys):
    # launch turns a registry that won't load (a RegistryError, so a ValueError) into its own refusal: exit 3, with the
    # reason recorded for `spark status`. The CLI's catch must leave that alone. (status's exit 0 on the same kind of
    # registry goes through cli.main too: test_a_registry_that_wont_load_is_said_and_the_rest_still_shown.)
    registry = tmp_path / "models.yaml"
    registry.write_text("budget: {allocatable_gib: 102}\n")
    monkeypatch.setitem(launch.main_launch.__kwdefaults__, "registry", registry)
    monkeypatch.setitem(launch.main_launch.__kwdefaults__, "state", tmp_path)
    monkeypatch.setattr(launch.os, "execvpe", lambda f, a, env: pytest.fail("must not exec"))
    assert cli.main(["launch", "coder", "--", "/bin/engine"]) == 3
    assert capsys.readouterr().err.startswith(f"spark: not starting coder: the registry {registry} won't load: ")
    assert launch.read_refusal(tmp_path)["model"] == "coder"


def test_the_commands_the_units_and_the_makefile_run_are_registered():
    parser = cli.build_parser()
    # llama-swap starts every engine as `spark launch <model> -- <engine command…>`: the -- stays.
    args = parser.parse_args(["launch", "m", "--", "/bin/x", "--port", "1"])
    assert args.rest == ["m", "--", "/bin/x", "--port", "1"]
    for argv in (["brake", "--key-env", "LLAMASWAP_KEY_SPARK"], ["models", "pull"], ["status"], ["apply"],
                 ["apply", "--dry-run"], ["apply", "--now"], ["render", "--out", "rendered"],
                 ["clients", "pi", "--write"], ["doctor"],
                 # make brake-release, and the Makefile's Phase 0 commands: make hooks, and make docs (Task 10's R5).
                 ["brake", "--release"], ["leakcheck", "--message", "/dev/null"], ["docs", "stack", "--write"],
                 ["docs", "check-scenarios"]):
        assert callable(parser.parse_args(argv).func), argv

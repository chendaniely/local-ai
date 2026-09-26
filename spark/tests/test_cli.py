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
    data["models"]["coder"]["footprint_gib"] = 70
    registry = tmp_path / "models.yaml"
    registry.write_text(yaml.safe_dump(data))
    out = tmp_path / "out"
    code = cli.main(["render", "--out", str(out), "--registry", str(registry), "--versions", str(FIX / "versions.yaml")])
    assert code == 1
    assert capsys.readouterr().err == ("spark render: the model set needs 92.0 GiB but the budget allows 78.0 GiB "
                                       "(allocatable 102 − reserve 24)\n")
    assert not out.exists()  # a refused render writes nothing


@pytest.mark.parametrize("bad", ["a missing registry", "a folder for a registry", "a registry that isn't YAML",
                                 "versions that aren't YAML"])
def test_a_file_that_cant_be_read_says_why_in_one_line(tmp_path, capsys, bad):
    # OSError (missing, a folder) and yaml.YAMLError (not YAML) are refusals too, not tracebacks.
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
    else:
        versions.write_text("components: {llama-swap: [\n")
    out = tmp_path / "out"
    code = cli.main(["render", "--out", str(out), "--registry", str(registry), "--versions", str(versions)])
    err = capsys.readouterr().err
    assert code == 1
    assert err.startswith("spark render: ") and "Traceback" not in err, err
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

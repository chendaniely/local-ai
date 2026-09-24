import pytest

from spark import cli


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == "spark 0.1.0"


def test_no_command_prints_help(capsys):
    assert cli.main([]) == 0
    assert "usage: spark" in capsys.readouterr().out

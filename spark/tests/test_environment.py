"""Every test runs in an environment it built (conftest.py's fake_environment), never the shell's."""

import os
import pwd


def test_every_test_runs_in_an_environment_it_built():
    # Dan's shell can hold secrets, and a failing test prints what the code under test was given: an assertion's
    # diff, a traceback's arguments. So nothing of the shell's environment is left but PATH and LANG, which find the
    # tools tests run and keep how they read text, and HOME is a new, empty folder. (Names and the home folder are
    # taken first: pytest prints the arguments of a call in a failing assert, and os.environ would be one.)
    names = set(os.environ) - {"PYTEST_CURRENT_TEST"}
    home, own = os.environ["HOME"], pwd.getpwuid(os.getuid()).pw_dir
    assert names <= {"PATH", "HOME", "LANG"}
    assert home != own and os.listdir(home) == []

"""Single-file test runner — execute the full test suite from PyCharm.

This file is an aggregator, not a collection of individual tests. Running it
executes every test module in ``tests/`` (unit + PostgreSQL/Redis integration)
in a single subprocess, so one execution in PyCharm gives maximal coverage.

Usage:

* PyCharm: right-click ``tests/test_all.py`` → Run ``pytest in test_all``,
  or run it as a plain Python script (``Run 'test_all'``).
* CLI: ``python tests/test_all.py`` or ``uv run pytest tests/test_all.py -v``.

The inner run ignores this file to avoid recursion. When ``tests/`` is run as
a whole (``uv run pytest tests/ -q``), the aggregator skips itself so the
suite never executes twice.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_THIS_FILE = Path(__file__).resolve()


def _run_suite() -> int:
    """Run the entire ``tests/`` directory in a fresh pytest subprocess.

    Returns:
        The pytest exit code (0 = all tests passed).
    """
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(_ROOT / "tests"),
        "--ignore",
        str(_THIS_FILE),
        "--rootdir",
        str(_ROOT),
        "-q",
    ]
    env = dict(os.environ)  # preserves TORTOISE_TEST_DB / docker env
    proc = subprocess.run(cmd, env=env, check=False)
    return proc.returncode


def test_full_suite(pytestconfig: pytest.Config) -> None:
    """Run every test module in ``tests/`` from a single PyCharm execution.

    The nested pytest subprocess replaces this process's stdout, so the full
    suite output (including PostgreSQL integration tests) appears in the IDE
    console.

    Skipped when this file was collected as part of a full-directory run
    (``pytest tests/``) so the suite is not executed twice.
    """
    args = [str(a) for a in pytestconfig.invocation_params.args]
    explicitly_run = any("test_all.py" in a for a in args)
    if not explicitly_run:
        pytest.skip(
            "test_all.py is an aggregator — run it directly "
            "(python tests/test_all.py or pytest tests/test_all.py)"
        )
    assert _run_suite() == 0, "test suite failed — see output above"


if __name__ == "__main__":
    sys.exit(_run_suite())

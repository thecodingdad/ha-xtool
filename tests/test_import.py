"""Tests for loading the integration package."""

from __future__ import annotations

import subprocess
import sys

from .features import requires


@requires("clean_first_import")
def test_first_import_succeeds() -> None:
    """A fresh interpreter imports the package on the first attempt.

    Home Assistant imports integrations in an executor; when that import
    fails it retries inside the event loop and logs a blocking-call warning.
    """
    result = subprocess.run(
        [sys.executable, "-c", "import custom_components.xtool"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]

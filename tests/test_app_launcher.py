import sys

import uvicorn

from docwizard import app as launcher


def test_windowed_build_without_console(monkeypatch, tmp_path):
    """Windows windowed builds have sys.stdout/stderr = None; startup must not crash."""
    monkeypatch.setenv("DOCWIZARD_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    launcher.ensure_streams()
    assert sys.stdout is not None and sys.stderr is not None
    print("hallo")  # must not raise
    uvicorn.Config(lambda *a: None, log_config=None).configure_logging()
    sys.stdout.close()

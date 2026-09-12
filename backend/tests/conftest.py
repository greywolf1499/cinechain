import sys
from pathlib import Path

import pytest


@pytest.fixture()
def config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point Settings at an isolated, throwaway /config for each test.

    app.db binds engine/settings at import time, so force a clean re-import
    per test to keep each test's SQLite file isolated.
    """
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    sys.modules.pop("app.db", None)
    from app.config import get_settings

    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()
    sys.modules.pop("app.db", None)

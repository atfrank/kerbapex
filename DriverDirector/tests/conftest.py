import pytest


@pytest.fixture(autouse=True)
def isolated_appdata(tmp_path, monkeypatch):
    """Tests must never touch the user's real %APPDATA%/DriverDirector (settings, manifest, templates)."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))

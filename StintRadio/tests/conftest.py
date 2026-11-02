"""Every test runs with its own empty settings folder, so no test can write to (or read from) the real %APPDATA%\\StintRadio:
audio.log, settings.json, saved laps and the voice caches there belong to the real app."""
import pytest


@pytest.fixture(autouse=True)
def _private_data_dir(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path_factory.mktemp("appdata")))

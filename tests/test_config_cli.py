import os

import pytest
from pydantic import ValidationError

from phobos.cli import initialize
from phobos.config import Settings


def test_init_never_overwrites_configuration(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    initialize()
    path = tmp_path / ".env"
    original = path.read_text()
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert len(original.splitlines()[0].split("=", 1)[1]) >= 32
    with pytest.raises(FileExistsError):
        initialize()
    assert path.read_text() == original


def test_config_requires_owner_token_and_valid_gateway(monkeypatch):
    monkeypatch.delenv("PHOBOS_API_TOKEN", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, api_token="x" * 32, gateway_url="file:///tmp/model")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, api_token="x" * 32, gateway_url="https://user:key@host/v1")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, api_token="x" * 32, gateway_url="https://:key@host/v1")

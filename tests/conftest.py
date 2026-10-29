import httpx
import pytest

from phobos.config import Settings
from phobos.store import Store


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        api_token="test-owner-token-with-32-characters",
        data_dir=tmp_path / "data",
        workspace_dir=tmp_path / "workspace",
        model="test-model",
        telegram_token="test-bot-token",
        telegram_users="123",
        telegram_channel="@testchannel",
    )


@pytest.fixture
def store(settings):
    return Store(settings.data_dir)


@pytest.fixture
def no_network():
    def reject(request):
        raise AssertionError("Unexpected network call")

    with httpx.Client(transport=httpx.MockTransport(reject)) as client:
        yield client

import os

import pytest

from caching_service.config import ENV_PREFIX


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # A developer's exported CACHE_SERVICE_* variables must not leak into any test.
    for name in os.environ:
        if name.startswith(ENV_PREFIX):
            monkeypatch.delenv(name)

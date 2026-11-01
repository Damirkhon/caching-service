import cache_cli
import caching_service
from cache_cli.__main__ import main


def test_packages_import() -> None:
    assert caching_service.__doc__
    assert cache_cli.__doc__
    assert main() == 0

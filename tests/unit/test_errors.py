import pytest

from caching_service.errors import CachingServiceError, PayloadNotFoundError, TransformerError


@pytest.mark.parametrize("error_type", [TransformerError, PayloadNotFoundError])
def test_domain_errors_share_a_base(error_type: type[CachingServiceError]) -> None:
    # The API layer relies on this base to tell deliberate domain errors from unexpected bugs.
    assert issubclass(error_type, CachingServiceError)

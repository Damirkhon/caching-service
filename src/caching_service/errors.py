"""Domain errors, raised by services and mapped to HTTP responses only in the API layer."""


class CachingServiceError(Exception):
    """Base for every error the service raises on purpose."""


class TransformerError(CachingServiceError):
    """The external transformer failed or timed out."""


class PayloadNotFoundError(CachingServiceError):
    """No payload exists with the requested identifier."""

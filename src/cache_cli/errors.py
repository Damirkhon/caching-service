"""Expected failures: each is reported as one line with an exit code, never as a traceback."""

import uuid
from http import HTTPStatus
from typing import Final

EXIT_OK: Final = 0
# The service is unreachable, misbehaved, or broke its contract.
EXIT_FAILURE: Final = 1
# Bad arguments or input; matches argparse's own exit code for usage errors.
EXIT_USAGE: Final = 2


class CliError(Exception):
    exit_code: int = EXIT_FAILURE


class UsageError(CliError):
    exit_code = EXIT_USAGE


class NoInputError(UsageError):
    def __init__(self) -> None:
        super().__init__("no request given: pass --input FILE or --json JSON, or pipe one on stdin")


class InvalidRequestError(UsageError):
    def __init__(self, reason: str) -> None:
        super().__init__(f"invalid request: {reason}")


class UnreadableInputError(UsageError):
    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"cannot read {path}: {reason}")


class UnwritableOutputError(UsageError):
    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"cannot write {path}: {reason}")


class ServiceUnavailableError(CliError):
    def __init__(self, host: str) -> None:
        super().__init__(f"cannot reach service at {host}")


class ServiceTimeoutError(CliError):
    """The service was reachable but did not answer in time."""

    def __init__(self, host: str) -> None:
        super().__init__(f"service at {host} did not answer in time")


class ServiceError(CliError):
    """The service answered with an unexpected status."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"service answered {status}: {detail}")
        self.status = status
        # A 422 means the service rejected our input, which is a usage error, not a failure.
        self.exit_code = EXIT_USAGE if status == HTTPStatus.UNPROCESSABLE_ENTITY else EXIT_FAILURE


class ContractError(CliError):
    """The service returned different payload ids for the same request."""

    def __init__(self, iteration: int, payload_id: uuid.UUID, first_id: uuid.UUID) -> None:
        super().__init__(
            f"service broke its contract: iteration {iteration} returned payload {payload_id}, "
            f"iteration 1 returned {first_id}"
        )

from unittest.mock import MagicMock

import pytest
from sqlalchemy import make_url

from caching_service.db.models import Payload
from caching_service.db.repositories import _insert_ignoring_conflicts


def session_for(dialect_name: str) -> MagicMock:
    # Only the dialect name is read, so a stand-in session is enough to pick the statement.
    session = MagicMock()
    session.get_bind.return_value.dialect.name = dialect_name
    return session


@pytest.mark.parametrize("url", ["postgresql+asyncpg://", "sqlite+aiosqlite://"])
def test_builds_on_conflict_do_nothing_per_dialect(url: str) -> None:
    dialect = make_url(url).get_dialect()()

    statement = _insert_ignoring_conflicts(session_for(dialect.name), Payload)

    assert "ON CONFLICT DO NOTHING" in str(statement.compile(dialect=dialect))


def test_rejects_unsupported_dialect() -> None:
    with pytest.raises(NotImplementedError, match="mysql"):
        _insert_ignoring_conflicts(session_for("mysql"), Payload)

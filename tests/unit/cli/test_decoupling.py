import subprocess
import sys

SERVER_MODULES = ("caching_service", "fastapi", "starlette", "sqlalchemy", "alembic")


def test_cli_does_not_import_server_dependencies() -> None:
    # A fresh, isolated interpreter, so modules already imported by the test session (the
    # server's included) can't mask a leak.
    probe = (
        "import sys, pkgutil, importlib, cache_cli as c\n"
        "for m in pkgutil.walk_packages(c.__path__, c.__name__ + '.'):\n"
        "    importlib.import_module(m.name)\n"
        "print(' '.join(sorted({n.split('.')[0] for n in sys.modules})))"
    )
    result = subprocess.run(  # noqa: S603 - fixed command, no user input
        [sys.executable, "-I", "-c", probe], capture_output=True, text=True, check=True
    )

    loaded = set(result.stdout.split())
    assert loaded.isdisjoint(SERVER_MODULES), loaded & set(SERVER_MODULES)

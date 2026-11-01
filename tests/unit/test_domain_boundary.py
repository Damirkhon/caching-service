import subprocess
import sys

FORBIDDEN = ("fastapi", "starlette", "sqlalchemy", "httpx", "pydantic")


def test_domain_does_not_import_frameworks() -> None:
    # A fresh, isolated interpreter, so modules already imported by the test session (or a stray
    # module in the working directory) can't mask a leak.
    probe = (
        "import sys, pkgutil, importlib, caching_service.domain as d\n"
        "for m in pkgutil.walk_packages(d.__path__, d.__name__ + '.'):\n"
        "    importlib.import_module(m.name)\n"
        "print(' '.join(sorted({n.split('.')[0] for n in sys.modules})))"
    )
    result = subprocess.run(  # noqa: S603 - fixed command, no user input
        [sys.executable, "-I", "-c", probe], capture_output=True, text=True, check=True
    )

    loaded = set(result.stdout.split())
    assert loaded.isdisjoint(FORBIDDEN), loaded & set(FORBIDDEN)

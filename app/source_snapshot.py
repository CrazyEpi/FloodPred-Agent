"""Request-local file reuse; final verification starts a fresh read epoch."""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

_files = ContextVar('source_files', default=None)


@contextmanager
def source_snapshot():
    token = _files.set({})
    try:
        yield
    finally:
        _files.reset(token)


def fresh_verification():
    cache = _files.get()
    if cache is not None:
        cache.clear()


def read_bytes(path):
    path = Path(path)
    cache = _files.get()
    if cache is None:
        return path.read_bytes()
    key = str(path.resolve())
    if key not in cache:
        cache[key] = path.read_bytes()
    return cache[key]

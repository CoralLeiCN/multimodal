"""Cooperative checkpoints for bounded search work, including lock waits."""

from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic

from app.services.embeddings import SearchError

_expires = ContextVar("search_deadline", default=None)


def timeout_error():
    return SearchError("Search took too long. Please try again.", "search_timeout", 504)


def remaining():
    expires = _expires.get()
    if expires is None:
        return None
    seconds = expires - monotonic()
    if seconds <= 0:
        raise timeout_error()
    return seconds


def check_deadline():
    remaining()


@contextmanager
def search_deadline(expires):
    token = _expires.set(expires)
    try:
        check_deadline()
        yield
    finally:
        _expires.reset(token)


@contextmanager
def deadline_lock(lock):
    seconds = remaining()
    acquired = lock.acquire() if seconds is None else lock.acquire(timeout=seconds)
    if not acquired:
        raise timeout_error()
    try:
        check_deadline()
        yield
    finally:
        lock.release()

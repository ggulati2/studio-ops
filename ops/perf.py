"""Request-scoped memo: values looked up many times while drawing one page (studio
settings, holidays, a month's day logs, every film's hours) are fetched once.

The store only exists while a request is being handled (RequestMemoMiddleware), so
management commands and background code always read fresh data. Any model save or
delete clears it, so a page drawn after a change in the same request is never stale."""
import threading

_local = threading.local()


def begin():
    _local.store = {}


def end():
    _local.store = None


def clear(*args, **kwargs):
    if getattr(_local, "store", None):
        _local.store.clear()


def memo(key, fn):
    store = getattr(_local, "store", None)
    if store is None:
        return fn()
    if key not in store:
        store[key] = fn()
    return store[key]


def peek(key):
    store = getattr(_local, "store", None)
    return store.get(key) if store else None

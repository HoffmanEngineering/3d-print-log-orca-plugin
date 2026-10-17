"""Which plugin callback, if any, the current thread is inside.

Orca keeps the audit context (and so `orca.host.plugin.storage()`) in thread_local state set by
each C++ -> Python trampoline. A thread the plugin starts itself has none.
"""

import contextlib
import threading

_state = threading.local()


def current():
    return getattr(_state, "kind", None)


@contextlib.contextmanager
def inside(kind):
    """kind: "slicing" (pipeline hook, slicing worker thread), "ui", or "event"."""
    previous = current()
    _state.kind = kind
    try:
        yield
    finally:
        _state.kind = previous

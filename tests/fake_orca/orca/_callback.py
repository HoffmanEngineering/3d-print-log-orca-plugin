"""Which plugin callback, if any, the current thread is inside, and what Orca audits there.

Orca keeps the audit context (and so `orca.host.plugin.storage()`) in thread_local state set by
each C++ -> Python trampoline. A thread the plugin starts itself has none, and neither has the
UI thread when it runs a notification's on_click (PluginHostUi.cpp calls it without a scope).

Kinds, named for the Orca thread the callback runs on:
- "slicing": the slicing worker (pipeline hooks, and lifecycle events raised from
  GCode.cpp / Print.cpp such as GCodeExportFinished). orca.host.ui must never be called here.
- "upload": the print-host upload queue thread (UploadStarted / UploadFinished).
- "device": the device manager (PrintStateChanged) and the multi-machine TaskManager.
- "event": any other lifecycle event.
- "ui": the UI thread inside a callback (a script's execute()).
"""

import contextlib
import threading

_state = threading.local()


def current():
    return getattr(_state, "kind", None)


def scoped_roots():
    return list(getattr(_state, "roots", ()))


@contextlib.contextmanager
def inside(kind, roots=()):
    """Run as Orca's trampoline would: `kind` names the thread; `roots` are the scoped allowed
    folders Orca adds for the call (the G-code folder for a slicing hook)."""
    previous = (current(), scoped_roots())
    _state.kind = kind
    _state.roots = list(roots)
    try:
        yield
    finally:
        _state.kind, _state.roots = previous

"""Which plugin callback, if any, the current thread is inside, and what Orca audits there.

Orca keeps the audit context (and so `orca.host.plugin.storage()`) in thread_local state set by
each C++ -> Python trampoline (PyPluginTrampoline.hpp). A thread the plugin starts itself has
none, and neither has the UI thread when it runs a notification's on_click or an HTML window's
on_message (PluginHostUi.cpp calls them without a scope).

Kinds, named for the Orca thread the callback runs on:
- "ui": the UI thread inside a callback: a script's execute(), a Pages tab's get_ui() and
  on_message(), and the lifecycle events raised there (SlicingJobComplete, PrintStateChanged,
  project, plate and preset events). A permission prompt here is modal.
- "slicing": the slicing worker (pipeline hooks, and events raised from GCode.cpp / Print.cpp
  such as GCodeExportFinished).
- "upload": the print-host upload queue thread (UploadStarted / UploadFinished).
- "job": the job worker (PrintJobStarted from PrintJob::process, SendJobStarted).
- "load": the plugin load worker (on_load).
Off the UI thread a prompt is posted for later and the call is denied.
"""

import contextlib
import threading

_state = threading.local()


def current():
    return getattr(_state, "kind", None)


def on_ui_thread():
    return current() == "ui"


def scoped_roots():
    return list(getattr(_state, "roots", ()))


def approved():
    """The categories the user said Yes to in this call. Orca records the approved stdlib frames
    (the ancestor cascade, PluginAuditManager.cpp), so the rest of that request passes."""
    return getattr(_state, "approved", None)


@contextlib.contextmanager
def inside(kind, roots=()):
    """Run as Orca's trampoline would: `kind` names the thread; `roots` are the scoped allowed
    folders Orca adds for the call (the G-code folder for a slicing hook)."""
    previous = (current(), scoped_roots(), approved())
    _state.kind = kind
    _state.roots = list(roots)
    _state.approved = set()
    try:
        yield
    finally:
        _state.kind, _state.roots, _state.approved = previous

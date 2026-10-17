"""Orca's CPython audit hook (src/slic3r/plugin/PluginAuditManager.cpp), as far as the plugin
can run into it.

Inside a plugin callback (an audit context), Orca checks:
- `_thread.start_new_thread`: always asks ("create a thread"). Off the UI thread the prompt is
  posted for later and the call is denied with PermissionError, so a thread started from an event
  handler fails the first time.
- file events (`open`, `os.mkdir`, `os.remove`, `os.rename`, `shutil.copyfile`, ...): allowed
  without a prompt inside data_dir() and the call's scoped roots; anything else asks (and off the
  UI thread, is denied).
Outside a callback (module import, a thread the plugin started, a notification's on_click)
nothing is audited.

Here every audited call that would prompt raises PermissionError, so a test fails if the plugin
would make Orca ask. Code on sys.path (lazy imports) is allowed, standing in for resources_dir().
"""

import os
import sys

from orca import _callback

data_dir = None  # set by orca_driver.reset

_FS_EVENTS = {
    "open": 1,
    "os.listdir": 1,
    "os.scandir": 1,
    "os.mkdir": 1,
    "os.remove": 1,
    "os.rmdir": 1,
    "os.truncate": 1,
    "shutil.rmtree": 1,
    "os.rename": 2,
    "shutil.copyfile": 2,
    "shutil.move": 2,
}


def _inside(path, root):
    try:
        path = os.path.normcase(os.path.abspath(os.fspath(path)))
        root = os.path.normcase(os.path.abspath(os.fspath(root)))
    except TypeError:
        return False
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _allowed(path):
    if isinstance(path, int) or path is None:
        return True  # an open file descriptor, or an event without a path
    roots = list(_callback.scoped_roots())
    if data_dir is not None:
        roots.append(data_dir)
    roots += [p for p in sys.path if p] + [sys.prefix, sys.base_prefix]
    return any(_inside(path, root) for root in roots)


def _hook(event, args):
    if _callback.current() is None:
        return
    if event == "_thread.start_new_thread":
        raise PermissionError("fake orca: a plugin callback started a thread (Orca asks first)")
    count = _FS_EVENTS.get(event)
    if count is None:
        return
    for path in args[:count]:
        if not _allowed(path):
            raise PermissionError(f"fake orca: {event} {path!r} outside the allowed roots")


_installed = False


def install():
    global _installed
    if not _installed:
        sys.addaudithook(_hook)
        _installed = True

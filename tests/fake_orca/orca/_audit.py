"""Orca's CPython audit hook (src/slic3r/plugin/PluginAuditManager.cpp), as far as the plugin
can run into it.

Inside a plugin callback (an audit context):
- file events inside data_dir() and the call's scoped roots pass without a prompt; anything else
  asks ("read the following file").
- network events (urllib.Request, http.client.*, socket.*), process events (os.startfile,
  subprocess.Popen, os.system) and `_thread.start_new_thread` ask.
- On the UI thread the prompt is modal: `answer(category, target)` plays the user (default Yes).
  Yes persists a target, and the rest of that call's request passes (the ancestor cascade). No
  raises PermissionError.
- Off the UI thread the prompt is posted for later and the call is denied with PermissionError.
  A later call passes only if the user had said Yes to that exact target already. Events with no
  target (socket.__new__, http.client.send) are never persisted, so urllib can never complete
  there.
Outside a callback (module import, a thread the plugin started, a notification's on_click)
nothing is audited. That is a documented gap, not consent, so when `strict` is on (orca_driver
turns it on) any I/O the plugin does there is recorded in `unaudited`, and the tests fail on it.

Code on sys.path (lazy imports) is allowed everywhere, standing in for resources_dir().
"""

import os
import sys

from orca import _callback

data_dir = None  # set by orca_driver.reset
strict = False
answer = None  # callable(category, target) -> bool; None answers Yes

prompts: list[tuple] = []  # (category, target, thread kind, allowed)
unaudited: list[tuple] = []  # (event, target): plugin I/O outside any audit context
audited: list[tuple] = []  # (category, event, thread kind): plugin I/O inside callbacks
granted: set = set()  # persisted (category, target) grants

_FILE_EVENTS = {
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
# event -> index of the target in its args, None for no target (PAM:708-730)
_NETWORK_EVENTS = {
    "urllib.Request": 0,
    "http.client.connect": 1,
    "http.client.send": None,
    "socket.getaddrinfo": 0,
    "socket.connect": 1,
    "socket.__new__": None,
}
_PROCESS_EVENTS = {"os.startfile": 0, "subprocess.Popen": 1, "os.system": 0}
_THREAD_EVENTS = {"_thread.start_new_thread": None}
_PLUGIN_DIR = f"{os.sep}printlog_orca{os.sep}"


def reset():
    global strict, answer
    strict = False
    answer = None
    prompts.clear()
    unaudited.clear()
    audited.clear()
    granted.clear()


def network_on_ui_thread_only():
    """True when every network event the plugin raised was on the UI thread, in a callback."""
    return all(kind == "ui" for category, _, kind in audited if category == "network")


def _inside(path, root):
    try:
        path = os.path.normcase(os.path.abspath(os.fspath(path)))
        root = os.path.normcase(os.path.abspath(os.fspath(root)))
    except TypeError:
        return False
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _code_path(path):
    roots = [p for p in sys.path if p] + [sys.prefix, sys.base_prefix]
    return any(_inside(path, root) for root in roots)


def _allowed_file(path):
    if isinstance(path, int) or path is None:
        return True  # an open file descriptor, or an event without a path
    roots = list(_callback.scoped_roots())
    if data_dir is not None:
        roots.append(data_dir)
    return _code_path(path) or any(_inside(path, root) for root in roots)


def _from_plugin():
    frame = sys._getframe(2)
    while frame is not None:
        if _PLUGIN_DIR in frame.f_code.co_filename:
            return True
        frame = frame.f_back
    return False


def _classify(event, args):
    """(category, target, needs a prompt) or None for an event Orca does not check."""
    count = _FILE_EVENTS.get(event)
    if count is not None:
        paths = [p for p in args[:count] if not isinstance(p, int) and p is not None]
        if all(_code_path(p) for p in paths):
            return None
        outside = [p for p in paths if not _allowed_file(p)]
        target = os.fspath(outside[0]) if outside else (os.fspath(paths[0]) if paths else "")
        return "file", target, bool(outside)
    for category, table in (
        ("network", _NETWORK_EVENTS),
        ("process", _PROCESS_EVENTS),
        ("thread", _THREAD_EVENTS),
    ):
        if event in table:
            index = table[event]
            target = "" if index is None or index >= len(args) else str(args[index])
            if category == "thread":
                target = "thread"
            return category, target, True
    return None


def _hook(event, args):
    found = _classify(event, args)
    if found is None:
        return
    category, target, needs_prompt = found
    kind = _callback.current()
    if kind is None:
        if strict and _from_plugin():
            unaudited.append((event, target))
        return
    audited.append((category, event, kind))
    if not needs_prompt:
        return
    approved = _callback.approved()
    if (category, target) in granted or (approved is not None and category in approved):
        return
    if kind != "ui":
        prompts.append((category, target, kind, False))
        raise PermissionError(f"fake orca: {event} {target!r} denied off the UI thread")
    yes = True if answer is None else bool(answer(category, target))
    prompts.append((category, target, kind, yes))
    if not yes:
        raise PermissionError(f"fake orca: the user declined {event} {target!r}")
    if target:
        granted.add((category, target))
    if approved is not None:
        approved.add(category)


_installed = False


def install():
    global _installed
    if not _installed:
        sys.addaudithook(_hook)
        _installed = True

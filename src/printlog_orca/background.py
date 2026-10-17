"""The plugin's one long-lived thread: uploads and notifications run here, never on Orca's threads.

Why one thread, started at import:
- Orca audits `_thread.start_new_thread` inside every plugin callback (PluginAuditManager.cpp).
  Off the UI thread the permission prompt is posted for later and the call is denied, so a
  thread started from an event handler or the slicing hook fails the first time. Module import
  runs outside any audit context, so the thread is started there and callbacks only enqueue.
- The thread has no audit context, so reading the plate's temp G-code (outside data_dir), the
  upload and opening the browser never prompt.
- orca.host.ui calls block until the UI thread runs them. From the slicing thread that can
  deadlock; from here it cannot.

Callbacks call submit() / notify_later(), which take a lock briefly and never wait on work.
"""

import collections
import logging
import threading
import time

_log = logging.getLogger("printlog_orca")


class Background:
    def __init__(self, notify_delay=1.5, clock=time.monotonic, name="3D Print Log"):
        self._notify_delay = notify_delay
        self._clock = clock
        self._name = name
        self._cond = threading.Condition()
        self._items = collections.deque()
        self._pending = None  # the debounced notification, if any
        self._deadline = 0.0
        self._busy = False
        self._stopping = False
        self._thread = None

    def start(self):
        """Starts the thread unless it runs. Call it outside Orca callbacks (at import)."""
        with self._cond:
            if self._thread is not None and self._thread.is_alive():
                return True
            self._stopping = False
            thread = threading.Thread(target=self._run, name=self._name, daemon=True)
            thread.start()
            self._thread = thread
            return True

    def running(self):
        with self._cond:
            return self._thread is not None and self._thread.is_alive() and not self._stopping

    def submit(self, fn):
        """Runs fn() on the thread, in order. False (and logged) if the thread is not running."""
        with self._cond:
            if not self._alive_locked():
                _log.warning("The 3D Print Log background thread is not running; dropped a task")
                return False
            self._items.append(fn)
            self._cond.notify_all()
            return True

    def notify_later(self, fn):
        """Runs fn() after notify_delay seconds without another notify_later(): a burst of slices
        (background slicing while editing) gives one notification, for the last one."""
        with self._cond:
            if not self._alive_locked():
                return False
            self._pending = fn
            self._deadline = self._clock() + self._notify_delay
            self._cond.notify_all()
            return True

    def wait_idle(self, timeout=10):
        """For tests and shutdown: True once nothing is queued, pending or running."""
        with self._cond:
            return self._cond.wait_for(self._idle_locked, timeout)

    def stop(self):
        with self._cond:
            self._stopping = True
            self._pending = None
            self._cond.notify_all()

    def _alive_locked(self):
        return self._thread is not None and self._thread.is_alive() and not self._stopping

    def _idle_locked(self):
        return not self._items and self._pending is None and not self._busy

    def _next_locked(self):
        while True:
            if self._items:
                return self._items.popleft()
            if self._stopping:
                return None
            if self._pending is None:
                self._cond.wait()
                continue
            remaining = self._deadline - self._clock()
            if remaining <= 0:
                fn, self._pending = self._pending, None
                return fn
            self._cond.wait(remaining)

    def _run(self):
        while True:
            with self._cond:
                fn = self._next_locked()
                if fn is None:
                    self._cond.notify_all()
                    return
                self._busy = True
            try:
                fn()
            except Exception:
                _log.exception("3D Print Log background task failed")
            finally:
                with self._cond:
                    self._busy = False
                    self._cond.notify_all()

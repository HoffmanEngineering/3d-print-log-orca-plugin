"""Drives the fake `orca` module the way OrcaSlicer drives a plugin.

load() runs discovery (PythonPluginBridge::finalize_plugin_capture): instantiate the
@orca.plugin class, call register_capabilities(), instantiate each capability. The other
helpers call into a capability inside the callback context Orca would set up, on the kind of
thread Orca would use.
"""

import importlib
import json
import os
import sys
from pathlib import Path

import orca
from orca import _audit, _callback
from orca.host import plugin as host_plugin
from orca.host import ui

# Which Orca thread raises each lifecycle event (see orca/_callback.py and the consent notes).
EVENT_THREADS = {
    "SliceStarted": "slicing",
    "SliceGeometryFinished": "slicing",
    "GCodeExportStarted": "slicing",
    "GCodeExportFinished": "slicing",
    "SlicingJobComplete": "ui",  # Plater::priv::on_process_completed
    "UploadStarted": "upload",
    "UploadFinished": "upload",
    "PrintStateChanged": "ui",  # DeviceManager, inside GUI_App's CallAfter
    "DeviceOnline": "ui",
    "DeviceOffline": "ui",
    "PrintJobStarted": "job",  # PrintJob::process on the job worker
    "PrintJobFinished": "job",
    "SendJobStarted": "job",
    "SendJobFinished": "job",
}


def reset(storage_dir):
    """A fresh Orca: no config, no notifications, no grants; strict audit tracking on."""
    orca._stored_configs.clear()
    ui.reset()
    _audit.reset()
    _audit.strict = True
    host_plugin.storage_dir = storage_dir
    # storage is <data_dir>/orca_plugins/plugin_data/<key>
    _audit.data_dir = Path(storage_dir).parents[2] if storage_dir is not None else None


def load(package="printlog_orca"):
    importlib.import_module(package)
    if len(orca._package_classes) != 1:
        raise AssertionError(f"expected one @orca.plugin class, got {orca._package_classes}")
    orca._registered.clear()
    orca._package_classes[0]().register_capabilities()
    capabilities = {}
    for cls in orca._registered:
        instance = cls()
        name = instance.get_name()
        if ";" in name or name in capabilities:
            raise AssertionError(f"bad or duplicate capability name {name!r}")
        capabilities[name] = instance
    return capabilities


def execute(capability, ctx):
    """A slicing-pipeline call on the slicing worker thread. Orca adds the G-code's folder as a
    scoped allowed root for the call."""
    roots = [os.path.dirname(ctx.gcode_path)] if ctx.gcode_path else []
    with _callback.inside("slicing", roots):
        return capability.execute(ctx)


def run_script(capability):
    """Plugins dialog -> Run, on the UI thread."""
    with _callback.inside("ui"):
        return capability.execute()


def fire(capabilities, event, ctx, thread=None):
    """Every enabled capability gets every event, on the raising thread. Orca logs and swallows a
    raise; tests see it."""
    kind = thread or EVENT_THREADS.get(event.name, "ui")
    for capability in capabilities.values():
        with _callback.inside(kind):
            capability.on_lifecycle_event(event, ctx)


def click(notification):
    """The user clicks the notification's hyper text: on the UI thread, outside any audit
    context. Returns what Orca would: True (or None) closes the notification."""
    result = notification.on_click()
    return result is None or bool(result)


def on_load(capabilities):
    for capability in capabilities.values():
        with _callback.inside("load"):
            capability.on_load()


def unload(capabilities):
    for capability in capabilities.values():
        with _callback.inside("ui"):
            capability.on_unload()


class Page:
    """A built Pages tab (PluginPage). The page's own HTML and script are not run here; tests
    send the messages its script would send. Whatever the plugin posts lands in `inbox`
    (CallAfter delivers it once the posting callback has returned)."""

    def __init__(self, capability):
        self.capability = capability
        self.inbox = []
        self.html = ""
        capability._orca_sender = self._deliver
        self.reload()

    def _deliver(self, message):
        self.inbox.append(json.loads(message))

    def reload(self):
        """The tab is first shown, or the user reloads it: get_ui() again."""
        with _callback.inside("ui"):
            self.html = self.capability.get_ui()
        return self.html

    def send(self, data):
        """window.orca.postMessage(data): JSON through the bridge, then on_message on the UI
        thread. Returns the messages the page received in reply."""
        before = len(self.inbox)
        decoded = json.loads(json.dumps(data))
        with _callback.inside("ui"):
            self.capability.on_message(decoded)
        return self.inbox[before:]

    def close(self):
        """The tab is removed (PluginPage::detach_capability)."""
        self.capability._orca_sender = None


class Browser:
    """webbrowser.open as CPython 3.12 runs it on Windows (WindowsDefault.open): the
    `webbrowser.open` event, then os.startfile(url), which Orca audits as a process launch. An
    OSError (a declined prompt) makes it return False."""

    def __init__(self):
        self.opened = []

    def __call__(self, url, new=0, autoraise=True):
        sys.audit("webbrowser.open", url)
        try:
            sys.audit("os.startfile", url)
        except OSError:
            return False
        self.opened.append(url)
        return True

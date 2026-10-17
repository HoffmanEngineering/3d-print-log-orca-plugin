"""Drives the fake `orca` module the way OrcaSlicer drives a plugin.

load() runs discovery (PythonPluginBridge::finalize_plugin_capture): instantiate the
@orca.plugin class, call register_capabilities(), instantiate each capability. The other
helpers call into a capability inside the callback context Orca would set up, on the kind of
thread Orca would use.
"""

import importlib
import os
from pathlib import Path

import orca
from orca import _audit, _callback
from orca.host import plugin as host_plugin
from orca.host import ui

# Which Orca thread raises each lifecycle event (see orca/_callback.py).
EVENT_THREADS = {
    "SliceStarted": "slicing",
    "SliceGeometryFinished": "slicing",
    "GCodeExportStarted": "slicing",
    "GCodeExportFinished": "slicing",
    "UploadStarted": "upload",
    "UploadFinished": "upload",
    "PrintStateChanged": "device",
    "PrintJobStarted": "device",
}


def reset(storage_dir):
    orca._stored_configs.clear()
    ui.reset()
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
    kind = thread or EVENT_THREADS.get(event.name, "event")
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
        with _callback.inside("ui"):
            capability.on_load()


def unload(capabilities):
    for capability in capabilities.values():
        with _callback.inside("ui"):
            capability.on_unload()

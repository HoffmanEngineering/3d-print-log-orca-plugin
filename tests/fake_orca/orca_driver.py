"""Drives the fake `orca` module the way OrcaSlicer drives a plugin.

load() runs discovery (PythonPluginBridge::finalize_plugin_capture): instantiate the
@orca.plugin class, call register_capabilities(), instantiate each capability. The other
helpers call into a capability inside the callback context Orca would set up.
"""

import importlib

import orca
from orca import _callback
from orca.host import plugin as host_plugin
from orca.host import ui


def reset(storage_dir):
    orca._stored_configs.clear()
    ui.calls.clear()
    host_plugin.storage_dir = storage_dir


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
    """A slicing-pipeline call on the slicing worker thread."""
    with _callback.inside("slicing"):
        return capability.execute(ctx)


def run_script(capability):
    """Plugins dialog -> Run, on the UI thread."""
    with _callback.inside("ui"):
        return capability.execute()


def fire(capabilities, event, ctx):
    """Every enabled capability gets every event. Orca logs and swallows a raise; tests see it."""
    for capability in capabilities.values():
        with _callback.inside("event"):
            capability.on_lifecycle_event(event, ctx)

"""orca.host.plugin (host/PluginHost.cpp)."""

from orca import _callback

storage_dir = None  # set by tests


def storage():
    # The real binding reads the thread_local audit context, which only plugin callbacks have.
    if _callback.current() is None:
        raise RuntimeError("plugin.storage() must be called from a plugin callback")
    if storage_dir is None:
        raise RuntimeError("fake orca: no storage folder set")
    return str(storage_dir)

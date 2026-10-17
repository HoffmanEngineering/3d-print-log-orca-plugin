"""A stand-in for OrcaSlicer's embedded `orca` module (2.5, main at fd28f6c).

Names and signatures follow src/slic3r/plugin/PythonPluginBridge.cpp and the capability
bindings. Only what the plugin touches is here. The real module is pybind11; enums here are
Python enums, which share `.name` and `==` with pybind11 enums.
"""

import enum
import json

from orca import _audit, _callback

# Orca installs its audit hook before any plugin code runs.
_audit.install()


class PluginType(enum.Enum):
    PrinterConnection = 0
    Pages = 1
    Analysis = 2
    Importer = 3
    Exporter = 4
    Visualization = 5
    Script = 6
    SlicingPipeline = 7
    ResourceTexture = 8
    Unknown = 9


class PluginResult(enum.Enum):
    Success = 0
    Skipped = 1
    RecoverableError = 2
    FatalError = 3


class LifecycleEvent(enum.Enum):
    NewProject = enum.auto()
    ProjectOpened = enum.auto()
    ProjectBeforeSave = enum.auto()
    ProjectAfterSave = enum.auto()
    ProjectClosed = enum.auto()
    ProjectDirtyChanged = enum.auto()
    SliceStarted = enum.auto()
    SliceGeometryFinished = enum.auto()
    GCodeExportStarted = enum.auto()
    GCodeExportFinished = enum.auto()
    SlicingJobComplete = enum.auto()
    ObjectAdded = enum.auto()
    ObjectDeleted = enum.auto()
    ObjectTransformed = enum.auto()
    ObjectChanged = enum.auto()
    ObjectRenamed = enum.auto()
    PlateCreated = enum.auto()
    PlateDeleted = enum.auto()
    PlateSelected = enum.auto()
    PlateRenamed = enum.auto()
    PresetSelected = enum.auto()
    PresetSaved = enum.auto()
    PrintStateChanged = enum.auto()
    DeviceOnline = enum.auto()
    DeviceOffline = enum.auto()
    DeviceDiscovered = enum.auto()
    DeviceSelected = enum.auto()
    UploadStarted = enum.auto()
    UploadFinished = enum.auto()
    PrintJobStarted = enum.auto()
    PrintJobFinished = enum.auto()
    SendJobStarted = enum.auto()
    SendJobFinished = enum.auto()


class LifecycleEvtCode(enum.Enum):
    Ok = 0
    Error = 1
    Warn = 2


class LifecycleEventContext:
    """Read-only in Orca (only a default constructor). Keyword arguments are a test convenience."""

    def __init__(self, **fields):
        self.name = ""
        self.code = LifecycleEvtCode.Ok
        self.msg = ""
        self.id = ""
        self.previous_name = ""
        self.device_id = ""
        self.job_id = ""
        self.source = ""
        self.index = -1
        self.dirty = False
        for key, value in fields.items():
            if not hasattr(self, key):
                raise AttributeError(key)
            setattr(self, key, value)


class PluginContext:
    def __init__(self):
        self.orca_version = ""


class ExecutionResult:
    def __init__(self, status=PluginResult.Success, message="", data=""):
        self.status = status
        self.message = message
        self.data = data

    @staticmethod
    def success(message="", data=""):
        return ExecutionResult(PluginResult.Success, message, data)

    @staticmethod
    def skipped(message=""):
        return ExecutionResult(PluginResult.Skipped, message, "")

    @staticmethod
    def failure(status, message, data=""):
        return ExecutionResult(status, message, data)


# The host keeps capability config in orca_plugins/config.json, keyed by capability. Tests
# reset it through orca_driver.
_stored_configs: dict[str, str] = {}


class PythonPluginBase:
    def get_name(self):
        raise RuntimeError("Tried to call pure virtual function get_name")

    def get_type(self):
        return PluginType.Unknown

    def on_load(self):
        pass

    def on_unload(self):
        pass

    def on_lifecycle_event(self, event, ctx):
        pass

    def has_config_ui(self):
        return False

    def get_config_ui(self):
        return ""

    def get_default_config(self):
        # The binding returns the default as a JSON string; a Python override returns a dict.
        return "{}"

    def get_config(self):
        return _stored_configs.get(self.get_name(), "{}")

    def get_config_version(self):
        return ""

    def save_config(self, config):
        try:
            json.loads(config)
        except ValueError:
            return False
        _stored_configs[self.get_name()] = config
        return True


class base:  # noqa: N801 - the real name
    def register_capabilities(self):
        pass


_package_classes: list[type] = []
_registered: list[type] = []


def plugin(cls):
    if not isinstance(cls, type):
        raise ValueError("@orca.plugin must decorate a class")
    if not issubclass(cls, base):
        raise ValueError("@orca.plugin must decorate a subclass of orca.base")
    _package_classes.append(cls)
    return cls


def register_capability(cls):
    if not (isinstance(cls, type) and issubclass(cls, PythonPluginBase)):
        raise ValueError("Registered class must inherit from a PluginCapability base")
    _registered.append(cls)


def request_permissions(fs_read=()):
    pass


# Submodules, bound as attributes the way pybind11's def_submodule does.
from orca import host, script, slicing  # noqa: E402

__all__ = [
    "ExecutionResult",
    "LifecycleEvent",
    "LifecycleEventContext",
    "LifecycleEvtCode",
    "PluginContext",
    "PluginResult",
    "PluginType",
    "PythonPluginBase",
    "_audit",
    "_callback",
    "base",
    "host",
    "plugin",
    "register_capability",
    "request_permissions",
    "script",
    "slicing",
]

"""orca.script: ScriptPluginCapability.cpp. execute() runs on the UI thread."""

import orca


class ScriptPluginCapabilityBase(orca.PythonPluginBase):
    def get_type(self):
        return orca.PluginType.Script

    def execute(self):
        raise RuntimeError("Tried to call pure virtual function execute")

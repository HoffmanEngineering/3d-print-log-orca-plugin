"""orca.slicing: SlicingPipelinePluginCapability.cpp."""

import enum

import orca


class Step(enum.Enum):
    posSlice = 0
    posPerimeters = 1
    posEstimateCurledExtrusions = 2
    posPrepareInfill = 3
    posInfill = 4
    posIroning = 5
    posContouring = 6
    posSupportMaterial = 7
    posDetectOverhangsForLift = 8
    posSimplifyPath = 9
    psWipeTower = 10
    psSkirtBrim = 11
    psGCodePostProcess = 12


def unscale(coord):
    return coord * 1e-6


class SlicingPipelineContext:
    """Fields are read-only in Orca. config_value() returns the serialized value or None."""

    def __init__(
        self,
        step=Step.psGCodePostProcess,
        gcode_path="",
        host="",
        output_name="",
        orca_version="2.5.0-alpha",
        config=None,
    ):
        self.orca_version = orca_version
        self.step = step
        self.gcode_path = gcode_path
        self.host = host
        self.output_name = output_name
        self.print = None
        self.object = None
        self._config = dict(config or {})

    def config_value(self, key):
        return self._config.get(key)

    def cancelled(self):
        return False


class SlicingPipelineCapabilityBase(orca.PythonPluginBase):
    def get_type(self):
        return orca.PluginType.SlicingPipeline

    def execute(self, ctx):
        raise RuntimeError("Tried to call pure virtual function execute")

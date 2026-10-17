"""orca.host.plater() / orca.host.model() (host/PluginHostApp.cpp, PluginHostModel.cpp): the live
project, in memory. project_path() requires the UI thread; ModelObject.name is what Orca shows in
the object list (for an STL, its file name, extension included). Tests set `project_path` and
`object_names`."""

from orca import _callback

project_path = ""  # "" while the project was never saved
object_names: list[str] = []


def reset():
    global project_path
    project_path = ""
    object_names.clear()


def _require_main_thread(name):
    if _callback.current() != "ui":
        raise RuntimeError(f"{name}() must be called from the UI thread")


class ModelObject:
    def __init__(self, name):
        self.name = name


class Model:
    def object_count(self):
        return len(object_names)

    def objects(self):
        return [ModelObject(name) for name in object_names]


class Plater:
    def model(self):
        return Model()

    def project_path(self):
        _require_main_thread("project_path")
        return project_path


def plater():
    return Plater()


def model():
    return Model()

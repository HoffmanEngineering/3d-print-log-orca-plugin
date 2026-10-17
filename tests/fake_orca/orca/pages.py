"""orca.pages: pluginTypes/pages/PagesPluginCapability.cpp and host/PluginPages.cpp.

get_ui() and on_message() run on the UI thread inside the plugin's audit context
(PagesPluginCapabilityTrampoline.hpp). post_message() hands the JSON to the page through
CallAfter, so the page sees it only after the current callback returns; before the page is built
it is dropped. orca_driver.Page plays the tab.
"""

import json

import orca


class PagesPluginCapabilityBase(orca.PythonPluginBase):
    def get_type(self):
        return orca.PluginType.Pages

    def get_ui(self):
        raise RuntimeError("Tried to call pure virtual function get_ui")

    def get_icon(self):
        return ""

    def on_message(self, data):
        pass

    def post_message(self, data):
        message = json.dumps(data)  # py_to_json: raises for a value JSON cannot hold
        sender = getattr(self, "_orca_sender", None)
        if sender is not None:
            sender(message)

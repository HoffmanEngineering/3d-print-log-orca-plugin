# /// script
# requires-python = ">=3.12"
# dependencies = []
#
# [tool.orcaslicer.plugin]
# name = "E2E event probe"
# description = "Test harness only: logs every lifecycle event Orca raises to the Python log."
# author = "3D Print Log e2e"
# version = "1.0.0"
# ///
"""Installed by the e2e harness next to the 3D Print Log plugin, never shipped.

Writes one line per lifecycle event to sys.stderr, which Orca tees into
<datadir>/log/python_*.log, so the harness can see which events the alpha raises, on which
thread, with which name and message.

Keep it to that: anything that opens a file outside the data folder from inside an Orca
callback (traceback's source lines, a lazy stdlib import) makes Orca show a permission prompt
in this plugin's name. An earlier version dumped thread stacks and did exactly that.
"""

import sys
import threading

import orca


class E2EProbe(orca.slicing.SlicingPipelineCapabilityBase):
    def get_name(self):
        return "E2E event probe"

    def execute(self, ctx):
        return orca.ExecutionResult.skipped()

    def on_lifecycle_event(self, event, ctx):
        try:
            name = getattr(event, "name", str(event))
            if name in ("ObjectChanged", "ProjectDirtyChanged"):
                return
            fields = []
            for key in ("id", "name", "code", "msg"):
                try:
                    fields.append(f"{key}={getattr(ctx, key)!r}")
                except Exception as error:  # noqa: BLE001
                    fields.append(f"{key}=<{error}>")
            thread = threading.current_thread().name
            sys.stderr.write(f"[E2E probe] event={name} thread={thread} {' '.join(fields)}\n")
        except Exception as error:  # noqa: BLE001
            sys.stderr.write(f"[E2E probe] error {error!r}\n")


@orca.plugin
class E2EProbePlugin(orca.base):
    def register_capabilities(self):
        orca.register_capability(E2EProbe)

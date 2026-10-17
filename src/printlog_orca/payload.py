"""The multipart body for POST /api/Slicer/uploads.

Parts: `gcode`, the gzipped UTF-8 windowed text; `metadata`, a JSON string with what the server
cannot read from the text (see the spec's API section).
"""

import gzip
import json
import uuid

# The API's caps (a 400 above them).
MAX_MODELS = 1000
MAX_USER_PRESETS = 64


def metadata(*, plugin_version, slicer_version, output_name, host, template, bounds, user_presets):
    return {
        "pluginVersion": plugin_version,
        "slicerVersion": slicer_version,
        "outputName": file_name(output_name),
        "host": host,
        "template": template,
        # Raw centerline bounds; the server adds the outer-wall line width, as ModelsPlaceholder.
        "models": [
            {
                "name": box.name,
                "minX": box.min_x,
                "maxX": box.max_x,
                "minY": box.min_y,
                "maxY": box.max_y,
                "maxZ": box.max_z,
            }
            for box in bounds[:MAX_MODELS]
        ],
        "userPresets": [
            {"kind": preset.kind, "name": preset.name, "json": preset.json}
            for preset in user_presets[:MAX_USER_PRESETS]
        ],
    }


def file_name(output_name):
    """The last path segment, split at either separator: an export path holds the user's
    account name, and a print-host name can carry a folder."""
    return output_name.replace("\\", "/").rsplit("/", 1)[-1]


def encode(gcode_text, metadata_fields):
    """(body, content type) of the multipart request."""
    gcode = gzip.compress(gcode_text.encode("utf-8"), mtime=0)
    meta = json.dumps(metadata_fields, ensure_ascii=False).encode("utf-8")
    boundary = uuid.uuid4().hex
    while boundary.encode() in gcode or boundary.encode() in meta:
        boundary = uuid.uuid4().hex

    def part(headers, content):
        return f"--{boundary}\r\n{headers}\r\n\r\n".encode() + content + b"\r\n"

    body = (
        part(
            'Content-Disposition: form-data; name="gcode"; filename="gcode.txt.gz"\r\n'
            "Content-Type: application/gzip",
            gcode,
        )
        + part(
            'Content-Disposition: form-data; name="metadata"\r\n'
            "Content-Type: application/json; charset=utf-8",
            meta,
        )
        + f"--{boundary}--\r\n".encode()
    )
    return body, f"multipart/form-data; boundary={boundary}"

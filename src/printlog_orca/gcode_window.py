"""Reads the head and tail of a G-code file, as PrintLog.SlicerSettings' GcodeWindow.ReadFromFile.

Slicers write the header, thumbnails and settings at the start and end of the file, so the
toolpath in between is dropped. The server parses exactly this text.
"""

WINDOW_SIZE = 2_000_000  # bytes per window; must exceed the largest thumbnail block
_BOM = "﻿"


def read(path):
    """The whole file when it is at most two windows, otherwise head + "\\n" + tail.

    Decoded as UTF-8 with a leading BOM removed. The tail may start inside a character; that
    becomes a replacement character in a line nobody reads.
    """
    with open(path, "rb") as file:
        file.seek(0, 2)
        size = file.tell()
        file.seek(0)
        if size <= WINDOW_SIZE * 2:
            return file.read().decode("utf-8", errors="replace").removeprefix(_BOM)

        head = file.read(WINDOW_SIZE)
        file.seek(-WINDOW_SIZE, 2)
        tail = file.read(WINDOW_SIZE)
    head_text = head.decode("utf-8", errors="replace").lstrip(_BOM)
    return head_text + "\n" + tail.decode("utf-8", errors="replace")

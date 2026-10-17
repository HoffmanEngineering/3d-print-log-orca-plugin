"""Per-object extrusion bounds, a port of PrintLog.SlicerSettings' ObjectBoundsScanner.

Orca brackets each instance's toolpath with "; printing object <name> id:<n> copy <m>" and
"; stop printing object ..." on every layer, tags runs with ";TYPE:" and layers with ";Z:".
Only extruding moves count, and brim, skirt, supports and towers are left out, so the box is the
model. The whole file is streamed as bytes; the windowed text the server gets has no toolpath.
"""

import math
from dataclasses import dataclass

BUFFER_SIZE = 1 << 20

_START = b"; printing object "
_STOP = b"; stop printing object"
_TYPE = b";TYPE:"
_LAYER = b";Z:"
_BOM = b"\xef\xbb\xbf"
_SKIPPED_TYPES = (b"Brim", b"Skirt", b"Support", b"Prime tower", b"Wipe tower", b"Custom")
# What .NET's Utf8Parser reads as a double ([+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?), less NaN and
# Infinity, which JSON cannot carry.
_NUMBER_CHARS = b"0123456789.+-eE"
_TAU = 2 * math.pi


@dataclass
class ObjectBounds:
    """The box one object instance's extrusions cover, in bed coordinates. Z is the top of the
    last layer that extruded."""

    name: str
    min_x: float = math.inf
    max_x: float = -math.inf
    min_y: float = math.inf
    max_y: float = -math.inf
    max_z: float = 0.0
    has_points: bool = False

    def add(self, x, y, z):
        self.min_x = min(self.min_x, x)
        self.max_x = max(self.max_x, x)
        self.min_y = min(self.min_y, y)
        self.max_y = max(self.max_y, y)
        self.max_z = max(self.max_z, z)
        self.has_points = True


def scan_file(path, log=lambda _: None):
    with open(path, "rb") as stream:
        return scan(stream, log)


def scan(stream, log=lambda _: None):
    """Bounds of every instance that extruded, in order of first appearance."""
    state = _ScanState(log)
    pending = b""
    bom_checked = False
    skipping_long_line = False
    while True:
        chunk = stream.read(BUFFER_SIZE)
        pending += chunk
        if not bom_checked:
            # A short first read must not skip the check.
            if len(pending) < len(_BOM) and chunk:
                continue
            bom_checked = True
            pending = pending.removeprefix(_BOM)

        lines = pending.split(b"\n")
        pending = lines.pop()
        for line in lines:
            if skipping_long_line:
                skipping_long_line = False
            elif len(line) >= BUFFER_SIZE:
                log(f"Skipping a G-code line longer than {BUFFER_SIZE} bytes")
            else:
                state.process(line.removesuffix(b"\r"))

        if not chunk:
            if pending and not skipping_long_line:
                state.process(pending.removesuffix(b"\r"))
            break

        if len(pending) >= BUFFER_SIZE:
            # Nothing we read is that long; drop the line and resync at the next newline.
            log(f"Skipping a G-code line longer than {BUFFER_SIZE} bytes")
            skipping_long_line = True
            pending = b""

    return [box for box in state.order if box.has_points]


def _number(token):
    # With only these characters, float() accepts exactly what _NUMBER matches; checking the
    # characters is much cheaper than the regex on the hot path.
    if token.translate(None, _NUMBER_CHARS):
        return None
    try:
        value = float(token)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _words(line):
    """The first "<letter><number>" of X, Y, E, I and J, stopping at a ';' comment. A letter
    whose first word is not a number is absent, as with the C# TryGetWord."""
    words = {}
    for token in line.split(b" "):
        if not token:
            continue
        letter = token[0]
        if letter == 0x3B:  # ';'
            break
        if letter in _AXES and len(token) > 1 and letter not in words:
            words[letter] = _number(token[1:])
    return words


_X, _Y, _E, _I, _J = _AXES = b"XYEIJ"
# A command is the first word: "G1" matches "G1 X1" and "G1", not "G10".
_LINEAR = (b"G0", b"G1")


class _ScanState:
    def __init__(self, log):
        self.log = log
        self.instances = {}
        self.order = []
        self.active = None
        self.skip_type = False
        self.x = self.y = self.z = 0.0
        self.relative_e = True
        self.last_e = 0.0

    def process(self, line):
        if not line:
            return
        first = line[0]
        if first == 0x47:  # 'G'
            self._move(line)
        elif first == 0x3B:  # ';'
            self._comment(line)
        elif first == 0x4D:  # 'M'
            command = line.partition(b" ")[0]
            if command == b"M82":
                self.relative_e = False
            elif command == b"M83":
                self.relative_e = True

    def _comment(self, line):
        if line.startswith(_START):
            self._start_instance(line[len(_START) :])
        elif line.startswith(_STOP):
            self.active = None
        elif line.startswith(_TYPE):
            self.skip_type = line[len(_TYPE) :].startswith(_SKIPPED_TYPES)
        elif line.startswith(_LAYER):
            value = _number(line[len(_LAYER) :].strip(b" "))
            if value is not None:
                self.z = value

    def _start_instance(self, marker):
        # "<name> id:<n> copy <m>": the name may contain spaces, so parse from the end. The whole
        # marker identifies the instance (each copy has its own id).
        copy_at = marker.rfind(b" copy ")
        id_at = -1 if copy_at < 0 else marker.rfind(b" id:", 0, copy_at)
        if id_at <= 0:
            self.log("Ignoring object marker without id/copy: " + marker.decode("utf-8", "replace"))
            self.active = None
            return
        self.active = self.instances.get(marker)
        if self.active is None:
            self.active = ObjectBounds(marker[:id_at].decode("utf-8", "replace"))
            self.instances[marker] = self.active
            self.order.append(self.active)

    def _move(self, line):
        command = line.partition(b" ")[0]
        clockwise = command == b"G2"
        is_arc = clockwise or command == b"G3"
        if not is_arc and command not in _LINEAR:
            if command == b"G92":
                reset = _words(line).get(_E)
                if reset is not None:
                    self.last_e = reset
            return

        words = _words(line)
        start_x, start_y = self.x, self.y
        moves = False
        if words.get(_X) is not None:
            self.x = words[_X]
            moves = True
        if words.get(_Y) is not None:
            self.y = words[_Y]
            moves = True

        # The filament position is tracked in both modes so a later M82 compares correctly.
        extrudes = False
        e = words.get(_E)
        if e is not None:
            delta = e if self.relative_e else e - self.last_e
            self.last_e += delta
            extrudes = delta > 0

        # A de-retraction (E without X/Y) has no path, so it never widens the box.
        if not moves or not extrudes or self.active is None or self.skip_type:
            return

        self.active.add(self.x, self.y, self.z)
        i, j = words.get(_I), words.get(_J)
        if is_arc and i is not None and j is not None:
            self._add_arc_extrema(start_x, start_y, start_x + i, start_y + j, clockwise)

    def _add_arc_extrema(self, x0, y0, cx, cy, clockwise):
        """An arc reaches its extreme X or Y where it crosses 0°, 90°, 180° or 270° around its
        center, which need not be an endpoint. Each such point within the sweep is added."""
        radius = math.hypot(x0 - cx, y0 - cy)
        start = math.atan2(y0 - cy, x0 - cx)
        end = math.atan2(self.y - cy, self.x - cx)
        sweep = _normalize(start - end) if clockwise else _normalize(end - start)
        if sweep < 1e-9 and radius > 0:
            sweep = _TAU  # coincident endpoints on a real circle: a full turn
        for quadrant in range(4):
            angle = quadrant * math.pi / 2
            offset = _normalize(start - angle) if clockwise else _normalize(angle - start)
            if offset <= sweep:
                x, y = cx + radius * math.cos(angle), cy + radius * math.sin(angle)
                self.active.add(x, y, self.z)


def _normalize(angle):
    angle = math.fmod(angle, _TAU)
    return angle + _TAU if angle < 0 else angle

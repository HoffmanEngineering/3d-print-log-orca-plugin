"""Finds the user's own preset files for the presets a G-code was sliced with.

Orca keeps them at <data_dir>/user/<account>/<kind>/<name>.json, holding only what the user
saved over the parent. The server uses them to tell saved changes from unsaved plater edits,
as the uploader's UserPresetLocator does. A preset that is not a user preset has no file and is
left out; so is one that is ambiguous or unreadable, which the server reads as "cannot tell".
"""

import json
from dataclasses import dataclass
from pathlib import Path

from printlog_orca import scrub

KINDS = ("process", "filament", "machine")
_MAX_INHERITS_DEPTH = 10
# Rejected on every platform so a G-code behaves the same wherever it is uploaded from.
_INVALID_NAME_CHARS = set('/\\:<>"|?*') | {chr(c) for c in range(32)}


@dataclass(frozen=True)
class UserPreset:
    kind: str  # Orca's folder name: process, filament or machine
    name: str
    json: str  # the preset file, less credential keys


def find(data_dir, print_settings_id, filament_settings_id, printer_settings_id, log):
    """User presets named by the three config values (as ctx.config_value serializes them),
    each followed by its `inherits` chain while the parent is a user preset too."""
    account_dirs = _account_dirs(Path(data_dir), log)
    if not account_dirs:
        return []

    wanted = [("process", parse_string(print_settings_id or ""))]
    wanted += [("filament", name) for name in parse_strings(filament_settings_id or "")]
    wanted += [("machine", parse_string(printer_settings_id or ""))]

    found = []
    seen = set()
    for kind, name in wanted:
        for _ in range(_MAX_INHERITS_DEPTH):
            if not name or (kind, name) in seen:
                break
            seen.add((kind, name))
            preset = _read(account_dirs, kind, name, log)
            if preset is None:
                break
            found.append(UserPreset(kind, name, _dump(scrub.scrub_preset(preset))))
            parent = preset.get("inherits")
            name = parent if isinstance(parent, str) else ""
    return found


def is_safe_preset_name(name):
    """The name comes from the G-code, so only a plain file name may be joined onto a path."""
    return name not in ("", ".", "..") and not (_INVALID_NAME_CHARS & set(name))


def data_dir_from_storage(storage):
    """Orca's data_dir(), from the plugin's storage folder: <data_dir>/orca_plugins/plugin_data/
    <key>, or .../plugin_data/_subscribed/<user>/<key> for a cloud plugin. Orca exposes no
    data_dir() to plugins."""
    for folder in Path(storage).parents:
        if folder.name == "orca_plugins":
            return folder.parent
    return None


def parse_string(serialized):
    """A single string option as Orca serializes it (escape_string_cstyle)."""
    out = []
    chars = iter(serialized)
    for c in chars:
        if c == "\\":
            c = next(chars, "")
            c = {"r": "\r", "n": "\n"}.get(c, c)
        out.append(c)
    return "".join(out)


def parse_strings(serialized):
    """A string-list option as Orca serializes it (escape_strings_cstyle): values joined by ';',
    quoted when they contain spaces or escapes. [] when malformed."""
    values = []
    i, n = 0, len(serialized)
    while True:
        while i < n and serialized[i] in " \t":
            i += 1
        if i == n:
            return values
        buffer = []
        if serialized[i] == '"':
            i += 1
            while i < n and serialized[i] != '"':
                c = serialized[i]
                if c == "\\":
                    i += 1
                    if i == n:
                        return []
                    c = {"r": "\r", "n": "\n"}.get(serialized[i], serialized[i])
                buffer.append(c)
                i += 1
            if i == n:
                return []
            i += 1
        else:
            while i < n and serialized[i] != ";":
                buffer.append(serialized[i])
                i += 1
        values.append("".join(buffer))
        while i < n and serialized[i] in " \t":
            i += 1
        if i == n:
            return values
        if serialized[i] != ";":
            return []
        i += 1
        if i == n:
            values.append("")
            return values


def _account_dirs(data_dir, log):
    user_dir = data_dir / "user"
    try:
        return [entry for entry in user_dir.iterdir() if entry.is_dir()]
    except FileNotFoundError:
        return []  # no user presets saved on this machine
    except OSError as error:
        log(f"No user presets: cannot list {user_dir}: {error}")
        return []


def _read(account_dirs, kind, name, log):
    if not is_safe_preset_name(name):
        log(f'{kind} preset name "{name}" is not a plain file name')
        return None
    candidates = [d / kind / f"{name}.json" for d in account_dirs]
    candidates = [path for path in candidates if path.is_file()]
    if len(candidates) != 1:
        if candidates:
            log(f'{kind} preset "{name}": {len(candidates)} files found, sending none')
        return None
    try:
        text = candidates[0].read_text(encoding="utf-8-sig")
        preset = json.loads(
            text,
            object_pairs_hook=_first_of_duplicates,
            parse_float=_RawNumber,
            parse_int=_RawNumber,
            parse_constant=_reject_constant,
        )
    except (OSError, ValueError) as error:
        log(f'{kind} preset "{name}": could not read {candidates[0]}: {error}')
        return None
    if not isinstance(preset, dict):
        log(f'{kind} preset "{name}": {candidates[0]} is not a JSON object')
        return None
    return preset


class _RawNumber(str):
    """A JSON number kept as written, so "0.20" reaches the server as 0.20."""


def _reject_constant(name):
    raise ValueError(f"{name} is not JSON")


def _first_of_duplicates(pairs):
    # Real preset files can repeat a key; the uploader's reader keeps the first.
    result = {}
    for key, value in pairs:
        result.setdefault(key, value)
    return result


def _dump(value):
    if isinstance(value, _RawNumber):
        return str.__str__(value)
    if isinstance(value, dict):
        items = (f"{json.dumps(k, ensure_ascii=False)}:{_dump(v)}" for k, v in value.items())
        return "{" + ",".join(items) + "}"
    if isinstance(value, list):
        return "[" + ",".join(_dump(v) for v in value) + "]"
    return json.dumps(value, ensure_ascii=False)

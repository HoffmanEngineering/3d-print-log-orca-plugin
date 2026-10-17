"""Blanks host and credential settings, with the rules of the API's SlicerSettingsScrubber.

The API scrubs again on arrival; this keeps the values from leaving the machine at all.
"""

import re

# Matched anywhere in the lower-cased key. Not "url" (slowdown_for_curled_perimeters) and not
# "host" (host_type is in Orca's note).
SENSITIVE_FRAGMENTS = (
    "print_host",
    "printhost",
    "apikey",
    "api_key",
    "password",
    "passwd",
    "token",
    "secret",
    "user",
    "cookie",
    "credential",
    "authorization",
    "access_code",
)

# post_process holds a script's command line: arguments that can be keys, and a local path.
SENSITIVE_KEYS = frozenset({"post_process"})

# Where .NET's EnumerateLines ends a line, which is how the server-side parser reads the text.
_LINE_BREAK = re.compile("\r\n|[\r\n\f\u0085  ]")
_SETTING = re.compile(r"[ \t]*;[ \t]*([A-Za-z0-9_.\-]+)[ \t]*[=:]")


def is_sensitive_key(key):
    lower = key.strip().lower()
    return lower in SENSITIVE_KEYS or any(fragment in lower for fragment in SENSITIVE_FRAGMENTS)


def scrub_text(text):
    """Blanks the value of every `; key = value` (or `; key: value`) line with a sensitive key.

    The key and separator stay, so the line still parses as an empty setting. Every other
    character is unchanged. Returns `text` itself when nothing needed scrubbing.
    """
    lower = text.lower()
    if not any(fragment in lower for fragment in (*SENSITIVE_FRAGMENTS, *SENSITIVE_KEYS)):
        return text

    pieces = []
    copied_to = 0
    line_start = 0
    for line_break in (*_LINE_BREAK.finditer(text), None):
        line_end = line_break.start() if line_break else len(text)
        setting = _SETTING.match(text, line_start, line_end)
        if setting and is_sensitive_key(setting.group(1)) and setting.end() < line_end:
            pieces.append(text[copied_to : setting.end()])
            copied_to = line_end
        if line_break is None:
            break
        line_start = line_break.end()

    if not pieces:
        return text
    pieces.append(text[copied_to:])
    return "".join(pieces)


def scrub_preset(preset):
    """A copy of a preset's settings without the sensitive keys."""
    return {key: value for key, value in preset.items() if not is_sensitive_key(key)}

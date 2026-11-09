"""Free text as a TOML string.

`json.dumps` is not one: it writes a character outside the Basic Multilingual Plane (an emoji) as a surrogate pair
`\\ud83d\\ude00`, which TOML rejects, so an exported `pyproject.toml` with an emoji in the workflow's name would not
install. A TOML basic string is the text in quotes with `\\`, `"` and control characters escaped; everything else,
Unicode included, is written as it is.
"""

from __future__ import annotations

_ESCAPES = {"\\": "\\\\", '"': '\\"', "\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r"}


def toml_string(text: str) -> str:
    out = []
    for ch in text.encode("utf-8", errors="replace").decode("utf-8"):  # a lone surrogate cannot be written
        if ch in _ESCAPES:
            out.append(_ESCAPES[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'

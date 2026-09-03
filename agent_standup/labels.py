"""Turn recorded filesystem paths into short, safe display labels.

Unlike cc-stats, every identifier this project keys on (a session's project,
a git repo) already *is* a real path --- there is no dash-encoded directory
name to decode here, only a path to shorten and mask. The shortening and
redaction-fallback approach is the same as cc-stats' ``labels.py``: strip a
leading home marker, keep the last couple of path segments, then run the
result through the vendored redactor so anything the shortening missed (an
account name embedded deeper in the path) is still caught.
"""

from ._vendor.redactor import redact_text

HOME_MARKERS = ("Users", "home")
TAIL_SEGMENTS = 2
HOME_LABEL = "~"
NO_PATH_LABEL = "(no cwd)"


def _shorten(path: str) -> str:
    """Last few components of a path, with a leading home prefix removed."""
    parts = [part for part in str(path).replace("\\", "/").split("/") if part]
    if len(parts) >= 2 and parts[0] in HOME_MARKERS:
        parts = parts[2:]
    if not parts:
        return HOME_LABEL
    return "-".join(parts[-TAIL_SEGMENTS:])


def pretty(path: str, redact: bool = True) -> str:
    """Display label for a recorded path (a session cwd or a git repo top)."""
    if not path:
        return NO_PATH_LABEL
    label = _shorten(path)
    return (redact_text(label) if redact else label) or HOME_LABEL


def pretty_path(path: object, redact: bool = True) -> str:
    """Mask a filesystem path before it appears in a message to the user."""
    text = str(path)
    return redact_text(text) if redact else text

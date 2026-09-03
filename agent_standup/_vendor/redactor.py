# Vendored from cc-replay (github.com/oh-namgyu/cc-replay) commit 04618de, MIT.
# sha256(original)=c39c12d12cd433b107be0144d6f9306bef12a5797eadd7af6893d69488f8ed4a
# Do not edit; sync from upstream.
"""Best-effort redaction of secrets and personal paths from a parsed session.

Redaction runs as two gates:

1. The structural gate walks the parsed session tree and masks string values in
   place (:func:`redact_session`, :func:`redact_value`).
2. The safety net re-runs the same patterns over the serialized session JSON
   just before it is embedded in the page (:func:`redact_text`).

Every pattern in :data:`PATTERNS` is value-safe: none of them can match a double
quote, a brace or a bracket, so running them over serialized JSON can only ever
rewrite characters inside a string value and never the JSON structure itself.
Coverage is best effort, not a guarantee.
"""

import re
from dataclasses import fields
from pathlib import Path
from typing import Any, List, Optional, Pattern, Sequence, Tuple

# Characters a masked value may consist of: everything except whitespace, the
# JSON/structural punctuation that must survive untouched, and the backslash —
# in serialized JSON a quote appears as \" and a value match that swallowed the
# backslash alone would break the string escaping around it.
_VALUE = r"[^\s\"'\\,;{}\[\]()]"
_TCHAR = r"[A-Za-z0-9_\-]"
_CRED_NAMES = r"api[_-]?key|token|secret|password|passwd|authorization"

PATTERNS = (
    # (label, compiled pattern, replacement)
    ("private-key", re.compile(
        r"-----BEGIN[^\"]{0,40}PRIVATE KEY-----[^\"]*?-----END[^\"]{0,40}PRIVATE KEY-----"),
     "[REDACTED:private-key]"),
    ("jwt", re.compile(r"eyJ" + _TCHAR + r"{6,}\.eyJ" + _TCHAR + r"{6,}\." + _TCHAR + r"{6,}"),
     "[REDACTED:jwt]"),
    ("anthropic-key", re.compile(r"sk-ant-" + _TCHAR + r"{12,}"), "[REDACTED:api-key]"),
    ("openai-key", re.compile(r"sk-" + _TCHAR + r"{16,}"), "[REDACTED:api-key]"),
    ("github-token", re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"), "[REDACTED:api-key]"),
    ("github-pat", re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "[REDACTED:api-key]"),
    ("slack-token", re.compile(r"xox[abprs]-[A-Za-z0-9-]{10,}"), "[REDACTED:api-key]"),
    ("aws-access-key-id", re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED:api-key]"),
    ("google-api-key", re.compile(r"AIza" + _TCHAR + r"{35}"), "[REDACTED:api-key]"),
    ("bearer-token", re.compile(r"(?i)(Bearer\s+)" + _TCHAR + r"{8,}"), r"\1[REDACTED:token]"),
    # Only the value is masked; the name, separator and any opening quote are
    # preserved. The lookaheads skip bare JSON literals and all-digit values so
    # that masking can never turn valid JSON into invalid JSON, and skip the
    # "Bearer" scheme word so the rule above keeps its more precise result.
    ("credential-assignment", re.compile(
        r"(?i)(\"?(?:" + _CRED_NAMES + r")\"?\s*[=:]\s*\"?)"
        r"(?!(?:null|true|false|bearer)\b)"
        r"(?=" + _VALUE + r"*[^\d\s\"',;{}\[\]()])" + _VALUE + r"+"),
     r"\1[REDACTED:credential]"),
    ("email", re.compile(r"[^\s\"'<>@,;:]+@[^\s\"'<>@,;:]+\.[A-Za-z]{2,}"), "[REDACTED:email]"),
    ("unix-home", re.compile(r"(?<![A-Za-z0-9])/(?:Users|home)/[^/\s\"'\\]+"), "~"),
    ("windows-home", re.compile(r"(?i)[A-Za-z]:\\{1,2}Users\\{1,2}[^\\/\s\"']+"),
     "%USERPROFILE%"),
)  # type: Tuple[Tuple[str, Pattern, str], ...]

_USER_PLACEHOLDER = "[REDACTED:user]"
_user_terms = None  # type: Optional[List[str]]


def home_terms() -> List[str]:
    """The account name, masked wherever it appears outside a home path.

    Home paths are also encoded into names elsewhere (Claude Code turns
    ``/Users/pat/code`` into the directory ``-Users-pat-code``), so the bare
    account name has to be masked as well or it leaks straight through.
    """
    global _user_terms
    if _user_terms is None:
        try:
            name = Path.home().name
        except (RuntimeError, OSError):
            name = ""
        _user_terms = [name] if len(name) >= 3 else []
    return _user_terms


def redact_text(text: str, terms: Optional[Sequence[str]] = None) -> str:
    """Mask every known secret shape in one string."""
    if not text:
        return text
    for _label, pattern, replacement in PATTERNS:
        text = pattern.sub(replacement, text)
    for term in (home_terms() if terms is None else terms):
        if term:
            text = text.replace(term, _USER_PLACEHOLDER)
    return text


def redact_value(value: Any, terms: Optional[Sequence[str]] = None) -> Any:
    """Recursively mask strings inside dicts, lists and scalars."""
    if isinstance(value, str):
        return redact_text(value, terms)
    if isinstance(value, list):
        return [redact_value(item, terms) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item, terms) for item in value)
    if isinstance(value, dict):
        return dict((key, redact_value(item, terms)) for key, item in value.items())
    return value


def redact_step(step: Any, terms: Optional[Sequence[str]] = None) -> None:
    """Mask every string field of a step and of its children, in place."""
    for spec in fields(step):
        current = getattr(step, spec.name)
        if isinstance(current, (str, list, dict)):
            setattr(step, spec.name, redact_value(current, terms))
    for child in step.children:
        redact_step(child, terms)


def redact_session(session: Any, terms: Optional[Sequence[str]] = None) -> Any:
    """Mask the session summary and its whole step tree, in place."""
    session.title = redact_text(session.title, terms)
    session.cwd = redact_text(session.cwd, terms)
    session.git_branch = redact_text(session.git_branch, terms)
    for step in session.steps:
        redact_step(step, terms)
    return session

# Vendored from cc-replay (github.com/oh-namgyu/cc-replay) commit 04618de, MIT.
# sha256(original)=9ef1d387c8a746819da23dab45e38a0fa064bef212bad319bf56b0aa4f198c87
# Do not edit; sync from upstream.
"""Parse Claude Code transcripts (.jsonl) into a normalized Session/Step model.

The format is undocumented and evolving, so parsing is forgiving: unrecognized
events become ``unknown`` steps, unparseable lines are skipped and counted.
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

MAX_TEXT_CHARS = 50000
MAX_TEXT_LINES = 1000
DEFAULT_MAX_EVENTS = 5000

# Bookkeeping event types: tallied into meta_counts, never rendered as steps.
META_TYPES = frozenset([
    "attachment", "mode", "bridge-session", "file-history-snapshot",
    "file-history-delta", "last-prompt", "ai-title", "queue-operation",
])

_USAGE_FIELDS = (("input_tokens", "input"), ("output_tokens", "output"),
                 ("cache_read_input_tokens", "cache_read"),
                 ("cache_creation_input_tokens", "cache_creation"))
_COUNT_KEYS ={"user": "user", "assistant": "assistant", "thinking": "thinking",
               "tool_call": "tool_calls", "system": "system",
               "unknown": "unknown", "subagent": "subagent"}


@dataclass
class Step:
    # One timeline entry; fields not relevant to ``kind`` keep their default.
    kind: str
    timestamp: str = ""
    text: str = ""
    tool_name: str = ""
    tool_input: str = ""
    tool_use_id: str = ""
    result: str = ""
    is_error: bool = False
    pending: bool = False
    subtype: str = ""
    raw: str = ""
    is_meta: bool = False
    children: List["Step"] = field(default_factory=list)


@dataclass
class Session:
    # A parsed transcript: summary metadata plus the ordered timeline.
    title: str = ""
    session_id: str = ""
    version: str = ""
    cwd: str = ""
    git_branch: str = ""
    started_at: str = ""
    ended_at: str = ""
    steps: List[Step] = field(default_factory=list)
    meta_counts: Dict[str, int] = field(default_factory=dict)
    counts: Dict[str, int] = field(default_factory=dict)
    tokens: Dict[str, int] = field(default_factory=dict)
    malformed_lines: int = 0
    dropped_steps: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def truncate(text: str) -> str:
    """Cut oversized payloads at parse time so the HTML page stays usable."""
    if not isinstance(text, str):
        text = str(text)
    kept = "\n".join(text[:MAX_TEXT_CHARS].split("\n")[:MAX_TEXT_LINES])
    if len(kept) == len(text):
        return text
    return kept + "\n… [truncated %d chars]" % (len(text) - len(kept))


def _pretty(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def _blocks(content: Any) -> List[Dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def _flatten(content: Any) -> str:
    """Flatten a message or tool_result content value into plain text."""
    if isinstance(content, str):
        return content
    parts = []  # type: List[str]
    for block in _blocks(content):
        value = block.get("text", block.get("content", ""))
        if isinstance(value, str):
            if value:
                parts.append(value)
        elif value not in (None, [], {}):
            parts.append(_pretty(value))
    return "\n".join(parts)


def _iter_events(path: Path) -> Iterator[Optional[Dict[str, Any]]]:
    """Yield one decoded event per line (``None`` when the line is unusable)."""
    with open(str(path), "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                event = None
            yield event if isinstance(event, dict) else None


def _user_steps(event: Dict[str, Any], results: Dict[str, Tuple[str, bool]]) -> List[Step]:
    """Build user steps, harvesting tool_result blocks into ``results``."""
    texts = []  # type: List[str]
    for block in _blocks((event.get("message") or {}).get("content")):
        kind = block.get("type")
        if kind == "tool_result":
            use_id = str(block.get("tool_use_id") or "")
            if use_id:
                results[use_id] = (truncate(_flatten(block.get("content"))),
                                   bool(block.get("is_error")))
        elif kind == "text" and str(block.get("text") or "").strip():
            texts.append(block["text"])
    if not texts:
        return []
    return [Step(kind="user", text=truncate("\n".join(texts)),
                 is_meta=bool(event.get("isMeta")))]


def _assistant_steps(event: Dict[str, Any]) -> List[Step]:
    steps = []  # type: List[Step]
    for block in _blocks((event.get("message") or {}).get("content")):
        kind = block.get("type")
        if kind == "text" and str(block.get("text") or "").strip():
            steps.append(Step(kind="assistant", text=truncate(block["text"])))
        elif kind == "thinking" and str(block.get("thinking") or "").strip():
            steps.append(Step(kind="thinking", text=truncate(block["thinking"])))
        elif kind == "tool_use":
            steps.append(Step(kind="tool_call",
                              tool_name=str(block.get("name") or "tool"),
                              tool_use_id=str(block.get("id") or ""),
                              tool_input=truncate(_pretty(block.get("input")))))
    return steps


def _steps_for(event: Dict[str, Any], results: Dict[str, Tuple[str, bool]]) -> List[Step]:
    kind = event.get("type")
    if kind == "user":
        return _user_steps(event, results)
    if kind == "assistant":
        return _assistant_steps(event)
    if kind == "system":
        return [Step(kind="system", text=truncate(_flatten(event.get("content"))),
                     subtype=str(event.get("subtype") or ""))]
    return [Step(kind="unknown", subtype=str(kind or "?"), raw=truncate(_pretty(event)))]


class _Builder:
    """Accumulates events into an ordered timeline."""

    def __init__(self) -> None:
        self.session = Session()
        self.results = {}  # type: Dict[str, Tuple[str, bool]]
        self.tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}
        self.meta = {}  # type: Dict[str, int]
        self.top = []  # type: List[Step]
        self.by_tool_id = {}  # type: Dict[str, Step]
        self.group = []  # type: List[Step]
        self.seen_messages = set()  # type: Set[str]
        self.ai_title = self.first_user = ""

    def _header(self, event: Dict[str, Any], stamp: str) -> None:
        if stamp:
            self.session.started_at = self.session.started_at or stamp
            self.session.ended_at = stamp
        for key, attr in (("sessionId", "session_id"), ("version", "version"),
                          ("cwd", "cwd"), ("gitBranch", "git_branch")):
            value = event.get(key)
            if isinstance(value, str) and value and not getattr(self.session, attr):
                setattr(self.session, attr, value)

    def _usage(self, event: Dict[str, Any]) -> None:
        """Sum usage once per message: streamed blocks repeat the same block."""
        message = event.get("message") or {}
        message_id = str(message.get("id") or event.get("uuid") or "")
        if message_id in self.seen_messages:
            return
        self.seen_messages.add(message_id)
        usage = message.get("usage")
        if not isinstance(usage, dict):
            return
        for key, name in _USAGE_FIELDS:
            value = usage.get(key)
            if isinstance(value, int):
                self.tokens[name] += value

    def _flush_group(self) -> None:
        if self.group:
            self.top.append(Step(kind="subagent", timestamp=self.group[0].timestamp,
                                 text="subagent", children=list(self.group)))
            del self.group[:]

    def _place(self, event: Dict[str, Any], steps: List[Step]) -> None:
        if event.get("isSidechain"):
            source = str(event.get("sourceToolUseID") or event.get("sourceToolUseId") or "")
            parent = self.by_tool_id.get(source)
            if parent is None:
                self.group.extend(steps)
            else:
                self._flush_group()
                parent.children.extend(steps)
            return
        self._flush_group()
        for step in steps:
            if step.kind == "tool_call" and step.tool_use_id:
                self.by_tool_id[step.tool_use_id] = step
            self.top.append(step)

    def feed(self, event: Dict[str, Any]) -> None:
        stamp = str(event.get("timestamp") or "")
        self._header(event, stamp)
        kind = event.get("type")
        if kind in META_TYPES:
            self.meta[str(kind)] = self.meta.get(str(kind), 0) + 1
            if not self.ai_title and isinstance(event.get("aiTitle"), str):
                self.ai_title = event["aiTitle"].strip()
            return
        if kind == "assistant":
            self._usage(event)
        steps = _steps_for(event, self.results)
        for step in steps:
            step.timestamp = stamp
            if not self.first_user and step.kind == "user" and not step.is_meta:
                self.first_user = step.text.strip().split("\n")[0][:120]
        if steps:
            self._place(event, steps)

    def finish(self, path: Path, max_events: int) -> Session:
        self._flush_group()
        session, top = self.session, self.top
        if max_events and max_events > 0 and len(top) > max_events:
            session.dropped_steps = len(top) - max_events
            top = top[-max_events:]
        session.steps = top
        session.meta_counts = self.meta
        session.tokens = self.tokens
        session.counts = _walk(top, self.results)
        session.counts["malformed"] = session.malformed_lines
        session.title = self.ai_title or self.first_user or path.stem
        return session


def _walk(steps: List[Step], results: Dict[str, Tuple[str, bool]],
          totals: Optional[Dict[str, int]] = None) -> Dict[str, int]:
    """Attach tool results (unmatched calls become pending) and tally counts."""
    if totals is None:
        totals = dict.fromkeys(["turns", "user", "assistant", "thinking", "tool_calls",
                                "errors", "system", "unknown", "subagent", "pending"], 0)
    for step in steps:
        key = _COUNT_KEYS.get(step.kind)
        if key:
            totals[key] += 1
        if step.kind in ("user", "assistant"):
            totals["turns"] += 1
        elif step.kind == "tool_call":
            found = results.get(step.tool_use_id)
            if found is None:
                step.pending = True
                totals["pending"] += 1
            else:
                step.result, step.is_error = found
                totals["errors"] += 1 if step.is_error else 0
        if step.children:
            _walk(step.children, results, totals)
    return totals


def parse_transcript(path: Path, max_events: int = DEFAULT_MAX_EVENTS) -> Session:
    """Parse a transcript file into a :class:`Session`, streaming line by line."""
    path = Path(path)
    builder = _Builder()
    for event in _iter_events(path):
        if event is None:
            builder.session.malformed_lines += 1
            continue
        builder.feed(event)
    return builder.finish(path, max_events)

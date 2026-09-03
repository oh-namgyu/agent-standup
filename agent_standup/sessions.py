"""Summarize Claude Code sessions into per-project activity in one time window.

Works directly over raw transcript events (like cc-stats' aggregator), rather
than the vendored parser's normalized step model, because the metric contract
dedups on identifiers that only exist on raw events (``message.id``,
``tool_use`` id). State is per transcript and released before the next one.

A "project" here is keyed by the FIRST valid ``cwd`` seen anywhere in the
transcript's event stream (attribution), not by the transcript's containing
directory. Every distinct ``cwd`` seen — including later drift away from the
first one — feeds a separate repo-candidate set for git discovery; only the
first valid one decides which project the session's activity is folded into.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from .scanner import ScanStats, TranscriptRef, find_transcripts, read_events

IDLE_CUTOFF_SECONDS = 15 * 60
NO_CWD = ""
TOKEN_KEYS = ("input", "output", "cache_read", "cache_creation")
_USAGE_FIELDS = (("input_tokens", "input"), ("output_tokens", "output"),
                 ("cache_read_input_tokens", "cache_read"),
                 ("cache_creation_input_tokens", "cache_creation"))


@dataclass
class ProjectSummary:
    """Accumulated activity for one project (keyed by its first-seen cwd)."""

    cwd: str = NO_CWD
    sessions: int = 0
    active_seconds: float = 0.0
    turns: int = 0
    tool_calls: int = 0
    tokens: Dict[str, int] = field(default_factory=lambda: dict.fromkeys(TOKEN_KEYS, 0))


@dataclass
class SessionStats:
    """Result of summarizing every transcript in a time window."""

    projects: Dict[str, ProjectSummary] = field(default_factory=dict)
    repo_candidates: Set[str] = field(default_factory=set)
    scan: ScanStats = field(default_factory=ScanStats)


def _as_utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_utc(moment).astimezone(timezone.utc)


def _blocks(content: Any) -> List[Dict[str, Any]]:
    if isinstance(content, list):
        return [block for block in content if isinstance(block, dict)]
    return []


def _has_user_text(content: Any) -> bool:
    if isinstance(content, str):
        return bool(content.strip())
    return any(block.get("type") == "text" and str(block.get("text") or "").strip()
               for block in _blocks(content))


def _cwd_of(event: Dict[str, Any]) -> str:
    value = event.get("cwd")
    return value if isinstance(value, str) and value else NO_CWD


@dataclass
class _FileState:
    # Working set for one transcript; discarded once its totals are folded in.
    first_cwd: str = NO_CWD
    cwd_candidates: Set[str] = field(default_factory=set)
    active_seconds: float = 0.0
    turns: int = 0
    tool_calls: int = 0
    tokens: Dict[str, int] = field(default_factory=lambda: dict.fromkeys(TOKEN_KEYS, 0))
    included: bool = False


def _scan_cwd(events: List[Dict[str, Any]]) -> Tuple[str, Set[str]]:
    """First valid cwd (attribution) and every distinct cwd seen (discovery)."""
    first = NO_CWD
    candidates = set()  # type: Set[str]
    for event in events:
        cwd = _cwd_of(event)
        if not cwd:
            continue
        candidates.add(cwd)
        if not first:
            first = cwd
    return first, candidates


def _windowed(events: List[Dict[str, Any]], start: datetime,
              until: datetime) -> List[Tuple[datetime, Dict[str, Any]]]:
    """Events with a parseable timestamp inside [start, until), stream order."""
    kept = []  # type: List[Tuple[datetime, Dict[str, Any]]]
    for event in events:
        moment = _parse_timestamp(event.get("timestamp"))
        if moment is not None and start <= moment < until:
            kept.append((moment, event))
    return kept


def _active_seconds(pairs: List[Tuple[datetime, Dict[str, Any]]]) -> float:
    """Sum of adjacent gaps <= the idle cutoff; negative or too-long gaps add 0."""
    total = 0.0
    for (prev, _e1), (curr, _e2) in zip(pairs, pairs[1:]):
        delta = (curr - prev).total_seconds()
        if 0 <= delta <= IDLE_CUTOFF_SECONDS:
            total += delta
    return total


def _fold_turns_and_tools(pairs: List[Tuple[datetime, Dict[str, Any]]], state: _FileState) -> None:
    assistant_ids = set()  # type: Set[str]
    tool_ids = set()  # type: Set[str]
    idless_tools = 0
    usage_by_id = {}  # type: Dict[str, Dict[str, int]]
    for index, (_moment, event) in enumerate(pairs):
        kind = event.get("type")
        message = event.get("message") or {}
        if kind == "user":
            if event.get("isMeta") or event.get("isSidechain"):
                continue
            if _has_user_text(message.get("content")):
                state.turns += 1
        elif kind == "assistant":
            blocks = _blocks(message.get("content"))
            if not event.get("isSidechain") and blocks:
                identifier = str(message.get("id") or event.get("uuid") or ("idx:%d" % index))
                assistant_ids.add(identifier)
            for block in blocks:
                if block.get("type") != "tool_use":
                    continue
                use_id = str(block.get("id") or "")
                if use_id:
                    tool_ids.add(use_id)
                else:
                    idless_tools += 1
            usage = message.get("usage")
            if isinstance(usage, dict):
                key = str(message.get("id") or event.get("uuid") or ("idx:%d" % index))
                usage_by_id[key] = usage  # last snapshot per message id wins
    state.turns += len(assistant_ids)
    state.tool_calls = len(tool_ids) + idless_tools
    for usage in usage_by_id.values():
        for raw, name in _USAGE_FIELDS:
            value = usage.get(raw)
            if isinstance(value, int):
                state.tokens[name] += value


def _process(ref: TranscriptRef, start: datetime, until: datetime) -> _FileState:
    events = [event for event in read_events(ref) if event is not None]
    state = _FileState()
    state.first_cwd, state.cwd_candidates = _scan_cwd(events)
    pairs = _windowed(events, start, until)
    if not pairs:
        return state
    state.included = True
    state.active_seconds = _active_seconds(pairs)
    _fold_turns_and_tools(pairs, state)
    return state


def _merge(stats: SessionStats, state: _FileState) -> None:
    summary = stats.projects.setdefault(state.first_cwd, ProjectSummary(cwd=state.first_cwd))
    summary.sessions += 1
    summary.active_seconds += state.active_seconds
    summary.turns += state.turns
    summary.tool_calls += state.tool_calls
    for key in TOKEN_KEYS:
        summary.tokens[key] += state.tokens[key]
    stats.repo_candidates |= state.cwd_candidates


def summarize_sessions(refs: Iterable[TranscriptRef], start: datetime, until: datetime,
                       stats: Optional[ScanStats] = None) -> SessionStats:
    """Fold every transcript's in-window activity into per-project totals."""
    start, until = _as_utc(start).astimezone(timezone.utc), _as_utc(until).astimezone(timezone.utc)
    result = SessionStats()
    result.scan = stats if stats is not None else ScanStats()
    for ref in refs:
        try:
            state = _process(ref, start, until)
        except OSError:
            result.scan.skip("unreadable")
            continue
        except Exception:  # a malformed transcript must never abort the run
            result.scan.skip("parse-error")
            continue
        result.scan.included += 1
        if state.included:
            _merge(result, state)
    return result


def summarize(start: datetime, until: datetime, root: Optional[Path] = None,
             project: Optional[str] = None) -> SessionStats:
    """Convenience: discover transcripts under ``root`` and summarize them."""
    stats = ScanStats()
    refs = find_transcripts(root=root, project=project, stats=stats)
    return summarize_sessions(refs, start, until, stats=stats)

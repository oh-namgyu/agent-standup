"""Find and read Claude Code transcripts across every project directory.

Strictly read-only: nothing here creates, writes or removes a file. Discovery is
a one-level glob (``<projects>/<project>/<session>.jsonl``) that never follows
symlinks and never descends further.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from ._vendor.parser import _iter_events

ENV_CONFIG_DIR = "CLAUDE_CONFIG_DIR"
TRANSCRIPT_SUFFIX = ".jsonl"


@dataclass
class TranscriptRef:
    """One transcript file and the project directory it came from."""

    path: Path
    project: str
    session_id: str


@dataclass
class ScanStats:
    """Counts describing what discovery and reading did with the candidates."""

    found: int = 0
    included: int = 0
    skipped: int = 0
    reasons: Dict[str, int] = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.skipped += 1
        self.reasons[reason] = self.reasons.get(reason, 0) + 1


def config_dir() -> Path:
    """Claude Code's config directory, honouring ``CLAUDE_CONFIG_DIR``."""
    override = os.environ.get(ENV_CONFIG_DIR)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".claude"


def projects_dir() -> Path:
    return config_dir() / "projects"


def _usable_dir(entry: Path) -> bool:
    """A real, visible directory: hidden names and symlinks are never entered."""
    if entry.name.startswith("."):
        return False
    if entry.is_symlink():
        return False
    return entry.is_dir()


def _usable_file(entry: Path) -> bool:
    if entry.name.startswith("."):
        return False
    if entry.is_symlink():
        return False
    return entry.suffix == TRANSCRIPT_SUFFIX and entry.is_file()


def _listdir(folder: Path, stats: ScanStats, reason: str) -> List[Path]:
    try:
        return sorted(folder.iterdir())
    except OSError:
        stats.skip(reason)
        return []


def find_transcripts(root: Optional[Path] = None, project: Optional[str] = None,
                     stats: Optional[ScanStats] = None) -> List[TranscriptRef]:
    """All candidate transcripts, sorted, one directory level deep."""
    root = projects_dir() if root is None else Path(root)
    stats = ScanStats() if stats is None else stats
    refs = []  # type: List[TranscriptRef]
    if not root.is_dir():
        return refs
    for folder in _listdir(root, stats, "unreadable-projects-dir"):
        if not _usable_dir(folder):
            continue
        if project is not None and folder.name != project:
            continue
        for entry in _listdir(folder, stats, "unreadable-project-dir"):
            if not _usable_file(entry):
                continue
            stats.found += 1
            refs.append(TranscriptRef(path=entry, project=folder.name,
                                      session_id=entry.stem))
    return refs


def read_events(ref: TranscriptRef) -> Iterator[Optional[Dict[str, Any]]]:
    """Stream one transcript's events; ``None`` marks an unusable line.

    Raises ``OSError`` if the file cannot be opened; callers count that as a
    skipped transcript.
    """
    return _iter_events(ref.path)

"""Discover git repositories from candidate cwds and collect their commit
history for a period, classifying each commit as AI-assisted or not by its
``Co-Authored-By`` trailer.

Every git invocation is a list argv (never a shell), runs with ``LC_ALL=C``,
and is time-bounded. Git's own ``--since``/``--until`` are only a prefilter —
this module's own ``%ct``-based comparison is the authoritative period filter.
"""

import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

DISCOVERY_TIMEOUT = 5
COLLECTION_TIMEOUT = 10
MIN_GIT_VERSION = (2, 22)
# %x01 marks a record start; a real one is always followed by a 40-hex hash.
_RECORD_START = re.compile(r"\x01([0-9a-f]{40})")
_C0 = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
FULL_PRETTY = r"%x01%H%x00%ct%x00%s%x00%(trailers:key=Co-Authored-By,valueonly,separator=%x2C)"
LEGACY_PRETTY = r"%x01%H%x00%ct%x00%s%x00"

_availability = None  # type: Optional[Tuple[bool, Optional[Tuple[int, int]]]]


@dataclass
class Commit:
    commit_hash: str
    authored_at: int
    subject: str
    co_authors: List[str] = field(default_factory=list)
    insertions: int = 0
    deletions: int = 0
    files_changed: int = 0
    ai_assisted: bool = False


@dataclass
class RepoLog:
    top: str
    commits: List[Commit] = field(default_factory=list)


@dataclass
class GitStats:
    disabled: bool = False
    disabled_reason: str = ""
    skipped: int = 0
    reasons: Dict[str, int] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def skip(self, reason: str) -> None:
        self.skipped += 1
        self.reasons[reason] = self.reasons.get(reason, 0) + 1

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def _env() -> Dict[str, str]:
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    return env


def probe_git() -> Tuple[bool, Optional[Tuple[int, int]]]:
    """(available, (major, minor)) for the system git; cached for the process."""
    global _availability
    if _availability is not None:
        return _availability
    try:
        result = subprocess.run(["git", "version"], capture_output=True, text=True,
                                timeout=DISCOVERY_TIMEOUT, env=_env())
    except (OSError, subprocess.TimeoutExpired):
        _availability = (False, None)
        return _availability
    match = re.search(r"(\d+)\.(\d+)", result.stdout)
    version = (int(match.group(1)), int(match.group(2))) if match else None
    _availability = (result.returncode == 0, version)
    return _availability


def reset_git_probe() -> None:
    """Test hook: clear the cached git-availability probe."""
    global _availability
    _availability = None


def _toplevel(cwd: str, stats: GitStats) -> Optional[str]:
    try:
        result = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                                capture_output=True, text=True, timeout=DISCOVERY_TIMEOUT,
                                env=_env())
    except subprocess.TimeoutExpired:
        stats.skip("timeout")
        return None
    except OSError:
        stats.skip("git-missing")
        return None
    if result.returncode != 0:
        stats.skip("non-repo")
        return None
    top = result.stdout.strip()
    return top or None


def discover(candidates: Iterable[str], stats: GitStats) -> Tuple[List[str], Dict[str, str]]:
    """Resolve candidates to repo top-levels; also return the per-candidate map.

    The returned list is the deduped, discovery-ordered set of toplevels (the
    same contract as :func:`discover_repos`). The map covers every candidate
    that resolved to a repo, pointing at the *canonical* toplevel string for
    that repo (the first one discovered for its real path), so a caller that
    wants to know which repo a specific cwd belongs to gets an answer that
    agrees with the returned list even when the repo was reached through more
    than one candidate cwd.
    """
    available, version = probe_git()
    if not available:
        stats.disabled = True
        stats.disabled_reason = "git-missing"
        stats.warn("git binary not found; the git axis is disabled")
        return [], {}
    if version is not None and version < MIN_GIT_VERSION:
        stats.warn("git %d.%d < 2.22: no trailer support, commits left unclassified"
                   % version)
    canonical = {}  # type: Dict[str, str]  # real path -> canonical top string
    tops = []  # type: List[str]
    mapping = {}  # type: Dict[str, str]  # candidate cwd -> canonical top string
    for cwd in candidates:
        top = _toplevel(cwd, stats)
        if top is None:
            continue
        real = str(Path(top).resolve())
        if real not in canonical:
            canonical[real] = top
            tops.append(top)
        mapping[cwd] = canonical[real]
    return tops, mapping


def discover_repos(candidates: Iterable[str], stats: GitStats) -> List[str]:
    """Resolve each candidate cwd to its repo top-level; dedup by real path."""
    tops, _mapping = discover(candidates, stats)
    return tops


def _clean(text: str) -> str:
    return _C0.sub("", text)


def _numstat_entries(blob: str) -> List[Tuple[int, int]]:
    """(added, deleted) per changed file; renames/copies and binaries tolerated.

    With ``-z --numstat`` a normal line is ``added\\tdeleted\\tpath``. A rename
    or copy instead leaves the path empty and follows with two more NUL-joined
    tokens (old name, new name). A binary file reports ``-`` for both counts.
    """
    tokens = blob.split("\x00")
    entries = []  # type: List[Tuple[int, int]]
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if not token:
            continue
        head = token.split("\t", 2)
        if len(head) != 3:
            continue
        added_raw, deleted_raw, path = head
        added = int(added_raw) if added_raw.isdigit() else 0
        deleted = int(deleted_raw) if deleted_raw.isdigit() else 0
        if not path:
            index = min(index + 2, len(tokens))  # skip the old-name/new-name pair
        entries.append((added, deleted))
    return entries


def _parse_records(raw: str) -> List[Tuple[str, str, str, str]]:
    """(hash, ct, subject, rest-blob) per record, tolerating stray 0x01 bytes."""
    matches = list(_RECORD_START.finditer(raw))
    records = []  # type: List[Tuple[str, str, str, str]]
    for index, match in enumerate(matches):
        body_start = match.end()
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
        parts = raw[body_start:body_end].split("\x00", 3)
        if len(parts) < 4:
            continue
        _leading, ct_str, subject, rest = parts
        records.append((match.group(1), ct_str, subject, rest))
    return records


def _commit_from_record(commit_hash: str, ct_str: str, subject: str, rest: str,
                        legacy: bool) -> Optional[Commit]:
    try:
        authored_at = int(ct_str)
    except ValueError:
        return None
    trailers_value, _sep, numstat_blob = rest.partition("\n")
    co_authors = []  # type: List[str]
    if not legacy and trailers_value:
        co_authors = [_clean(value).strip() for value in trailers_value.split(",")
                     if value.strip()]
    added = deleted = files = 0
    for a, d in _numstat_entries(numstat_blob):
        added += a
        deleted += d
        files += 1
    return Commit(commit_hash=commit_hash, authored_at=authored_at,
                 subject=_clean(subject), co_authors=co_authors, insertions=added,
                 deletions=deleted, files_changed=files,
                 ai_assisted=any("claude" in author.lower() for author in co_authors))


def _parse_log(raw: str, start: datetime, until: datetime, legacy: bool) -> List[Commit]:
    commits = []  # type: List[Commit]
    for commit_hash, ct_str, subject, rest in _parse_records(raw):
        commit = _commit_from_record(commit_hash, ct_str, subject, rest, legacy)
        if commit is None:
            continue
        moment = datetime.fromtimestamp(commit.authored_at, tz=timezone.utc)
        if start <= moment < until:  # authoritative filter: git's own is a prefilter
            commits.append(commit)
    return commits


def collect_repo(top: str, start: datetime, until: datetime, stats: GitStats) -> RepoLog:
    _available, version = probe_git()
    legacy = version is not None and version < MIN_GIT_VERSION
    pretty = LEGACY_PRETTY if legacy else FULL_PRETTY
    argv = ["git", "-C", top, "-c", "core.pager=cat", "log", "--no-merges",
           "--since=%s" % start.isoformat(), "--until=%s" % until.isoformat(),
           "-z", "--numstat", "--pretty=format:" + pretty]
    try:
        result = subprocess.run(argv, capture_output=True, text=True,
                                timeout=COLLECTION_TIMEOUT, env=_env())
    except subprocess.TimeoutExpired:
        stats.skip("timeout")
        return RepoLog(top=top)
    except OSError:
        stats.skip("git-missing")
        return RepoLog(top=top)
    if result.returncode != 0:
        stats.skip("log-failed")
        return RepoLog(top=top)
    return RepoLog(top=top, commits=_parse_log(result.stdout, start, until, legacy))


def collect(candidates: Iterable[str], start: datetime, until: datetime,
           stats: Optional[GitStats] = None) -> Tuple[List[RepoLog], Dict[str, str], GitStats]:
    """Discover repos among ``candidates`` and collect each one's commit log.

    Returns the per-repo logs, the candidate-cwd -> repo-top map from
    :func:`discover` (for linking a session's project back to its repo), and
    the stats collected along the way.
    """
    stats = stats if stats is not None else GitStats()
    tops, mapping = discover(candidates, stats)
    logs = [collect_repo(top, start, until, stats) for top in tops]
    return logs, mapping, stats

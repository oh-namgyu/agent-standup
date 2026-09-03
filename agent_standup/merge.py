"""Join session summaries and git commit logs into one report model.

Kept deliberately separate from its two source axes rather than trying to
reconcile them into one row per "project": a session's cwd and a commit's
repo top-level are different kinds of identifiers that only sometimes line
up, and collapsing them risks double-attributing the same work in a monorepo
where several sessions share one repo, or one session touches several. The
report instead presents both axes side by side and links a project to a repo
only through the one relationship that is unambiguous --- the project's own
recorded cwd resolving to a git top-level.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from .gitlog import Commit, GitStats, RepoLog
from .labels import pretty as label_for
from .sessions import ProjectSummary, SessionStats


@dataclass
class ProjectEntry:
    cwd: str
    label: str
    sessions: int
    active_seconds: float
    turns: int
    tool_calls: int
    tokens: Dict[str, int]
    related_repos: List[str] = field(default_factory=list)  # repo display labels


@dataclass
class RepoEntry:
    top: str
    label: str
    ai_commits: List[Commit] = field(default_factory=list)
    other_count: int = 0
    other_insertions: int = 0
    other_deletions: int = 0


@dataclass
class Totals:
    sessions: int = 0
    active_seconds: float = 0.0
    ai_commits: int = 0
    other_commits: int = 0
    files_changed: int = 0
    repos_skipped: int = 0
    files_skipped: int = 0


@dataclass
class Report:
    start: datetime
    until: datetime
    projects: List[ProjectEntry] = field(default_factory=list)
    repos: List[RepoEntry] = field(default_factory=list)
    totals: Totals = field(default_factory=Totals)
    redacted: bool = True
    git_disabled: bool = False
    git_disabled_reason: str = ""
    warnings: List[str] = field(default_factory=list)


def _project_entry(cwd: str, summary: ProjectSummary, repo_map: Dict[str, str],
                   redact: bool) -> ProjectEntry:
    top = repo_map.get(cwd)
    related = [label_for(top, redact)] if top else []
    return ProjectEntry(cwd=cwd, label=label_for(cwd, redact), sessions=summary.sessions,
                        active_seconds=summary.active_seconds, turns=summary.turns,
                        tool_calls=summary.tool_calls, tokens=dict(summary.tokens),
                        related_repos=related)


def _repo_entry(log: RepoLog, redact: bool) -> Optional[RepoEntry]:
    if not log.commits:
        return None
    ai = [commit for commit in log.commits if commit.ai_assisted]
    other = [commit for commit in log.commits if not commit.ai_assisted]
    return RepoEntry(top=log.top, label=label_for(log.top, redact), ai_commits=ai,
                     other_count=len(other),
                     other_insertions=sum(commit.insertions for commit in other),
                     other_deletions=sum(commit.deletions for commit in other))


def build_report(stats: SessionStats, logs: List[RepoLog], repo_map: Dict[str, str],
                 git_stats: GitStats, start: datetime, until: datetime,
                 redact: bool = True) -> Report:
    """Assemble the final :class:`Report` from both axes; sorts deterministically.

    Projects sort by active time descending, then by label, so the busiest
    work leads and ties break on a stable, human-readable key rather than
    dict iteration order. Repos sort by total commit count descending, then
    by label, for the same reason.
    """
    projects = [_project_entry(cwd, summary, repo_map, redact)
               for cwd, summary in stats.projects.items()]
    projects.sort(key=lambda entry: (-entry.active_seconds, entry.label))

    repos = [entry for entry in (_repo_entry(log, redact) for log in logs)
            if entry is not None]
    repos.sort(key=lambda entry: (-(len(entry.ai_commits) + entry.other_count), entry.label))

    totals = Totals(
        sessions=sum(entry.sessions for entry in projects),
        active_seconds=sum(entry.active_seconds for entry in projects),
        ai_commits=sum(len(entry.ai_commits) for entry in repos),
        other_commits=sum(entry.other_count for entry in repos),
        files_changed=sum(commit.files_changed for log in logs for commit in log.commits),
        repos_skipped=git_stats.skipped,
        files_skipped=stats.scan.skipped,
    )
    return Report(start=start, until=until, projects=projects, repos=repos, totals=totals,
                 redacted=redact, git_disabled=git_stats.disabled,
                 git_disabled_reason=git_stats.disabled_reason,
                 warnings=list(git_stats.warnings))

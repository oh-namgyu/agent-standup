"""Command line interface for agent-standup.

stdout carries exactly the report (or, with an output file, the path to it),
so the command composes in a pipeline. Progress, warnings and errors all go
to stderr. A fail-closed self-check runs on the fully rendered text before
anything is written or printed: if it still finds anything that looks like
a home path, the account name, or an unmasked secret shape, nothing is
emitted at all.

Exit codes: 0 success (warnings possible), 1 nothing to report or the
self-check tripped, 2 a usage error.
"""

import argparse
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import IO, List, Optional, Tuple

from . import __version__
from ._vendor.redactor import PATTERNS, home_terms
from .gitlog import GitStats, collect
from .labels import pretty_path
from .merge import Report, build_report
from .report_html import render as render_html
from .report_md import render as render_md
from .scanner import ScanStats, find_transcripts
from .sessions import summarize_sessions

DEFAULT_SINCE = "7d"
DEFAULT_HTML_OUTPUT = "standup.html"
_RELATIVE = re.compile(r"^(\d+)\s*d(?:ays?)?$", re.IGNORECASE)
_DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")
_HOME_PREFIX = re.compile(r"(?:(?<![A-Za-z0-9])/(?:Users|home)/|\\Users\\)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-standup",
        description="Join Claude Code session activity with git commit history "
                    "into a paste-ready standup report.")
    parser.add_argument("--since", default=DEFAULT_SINCE, metavar="WHEN",
                        help="start of the period: relative (7d, 14d) or an "
                             "absolute date/time (default: %(default)s)")
    parser.add_argument("--until", default=None, metavar="WHEN",
                        help="end of the period, exclusive (default: now)")
    parser.add_argument("-o", "--output", type=Path, default=None, metavar="PATH",
                        help="write the report to PATH instead of stdout")
    parser.add_argument("--html", action="store_true",
                        help="render a self-contained HTML page instead of Markdown "
                             "(default file: %s if -o is not given)" % DEFAULT_HTML_OUTPUT)
    # Project directory names begin with a dash, which argparse would read as
    # another flag, so the equals form is the one to document.
    parser.add_argument("--project", default=None, metavar="NAME",
                        help="limit to one Claude Code project directory; names "
                             "start with a dash, so write --project=-Users-you-code")
    parser.add_argument("--utc", action="store_true",
                        help="interpret relative and date-only periods in UTC "
                             "instead of the local time zone")
    parser.add_argument("--no-redact", action="store_true",
                        help="show raw paths and subjects, and skip the fail-closed "
                             "self-check (redaction is on by default)")
    parser.add_argument("--no-git", action="store_true",
                        help="skip the git axis entirely (transcript activity only)")
    parser.add_argument("--version", action="version",
                        version="agent-standup " + __version__)
    return parser


def _localize(naive: datetime, utc: bool) -> datetime:
    """Attach a zone to a naive wall-clock value, honouring DST for its date.

    ``naive.astimezone()`` with no argument asks the system zone database for
    the correct offset *as of that specific date*, unlike reusing "now"'s
    offset, which drifts by an hour across a DST boundary.
    """
    return naive.replace(tzinfo=timezone.utc) if utc else naive.astimezone()


def _parse_when(value: str, utc: bool, now_utc: datetime) -> Optional[datetime]:
    """A relative "Nd" span before ``now``, or an absolute date/time; UTC out."""
    text = value.strip()
    match = _RELATIVE.match(text)
    if match:
        days = int(match.group(1))
        if utc:
            return now_utc - timedelta(days=days)
        naive_now = now_utc.astimezone().replace(tzinfo=None)
        return _localize(naive_now - timedelta(days=days), utc=False).astimezone(timezone.utc)
    for fmt in _DATE_FORMATS:
        try:
            naive = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return _localize(naive, utc).astimezone(timezone.utc)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = _localize(parsed, utc)
    return parsed.astimezone(timezone.utc)


def resolve_period(since: str, until: Optional[str],
                   utc: bool) -> Optional[Tuple[datetime, datetime]]:
    """One absolute ``[start, until)`` instant pair in UTC; ``None`` on bad input."""
    now_utc = datetime.now(timezone.utc)
    until_moment = now_utc if until is None else _parse_when(until, utc, now_utc)
    start_moment = _parse_when(since, utc, now_utc)
    if start_moment is None or until_moment is None:
        return None
    return start_moment, until_moment


def selfcheck(text: str) -> List[str]:
    """Reasons the final text should not be emitted; empty means it is clean."""
    hits = []  # type: List[str]
    for term in home_terms():
        if term and term in text:
            hits.append("account name")
    if _HOME_PREFIX.search(text):
        hits.append("raw home-path prefix")
    for label, pattern, _replacement in PATTERNS:
        if pattern.search(text):
            hits.append("unmasked %s pattern" % label)
    return list(dict.fromkeys(hits))  # de-duplicate, keep first-seen order


def write_atomic(target: Path, text: str) -> None:
    """Write through a temp file in the same directory, then rename over."""
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(target.parent), prefix=".agent-standup-",
        suffix=".tmp", delete=False)
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(target))
    except BaseException:  # includes KeyboardInterrupt, so Ctrl-C cleans up too
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def _scan_report(scan: ScanStats, err: IO[str]) -> None:
    err.write("scanned %d transcripts: %d read, %d skipped\n"
              % (scan.found, scan.included, scan.skipped))
    if scan.skipped:
        details = ", ".join("%s %s" % (count, reason)
                            for reason, count in sorted(scan.reasons.items()))
        err.write("warning: %d transcripts could not be read (%s)\n" % (scan.skipped, details))


def _emit(report: Report, args: argparse.Namespace, out: IO[str], err: IO[str]) -> int:
    text = render_html(report) if args.html else render_md(report)
    if not args.no_redact:
        hits = selfcheck(text)
        if hits:
            err.write("agent-standup: refusing to emit output -- self-check found: %s\n"
                      % ", ".join(hits))
            return 1

    output = args.output
    if output is None and args.html:
        output = Path(DEFAULT_HTML_OUTPUT)
        err.write("agent-standup: --html with no -o -- writing %s\n" % output)

    if output is None:
        out.write(text)
    else:
        try:
            write_atomic(output, text)
        except OSError as failure:
            err.write("agent-standup: could not write %s (%s)\n"
                      % (pretty_path(output, not args.no_redact), failure))
            return 2
        out.write("%s\n" % output.resolve())

    totals = report.totals
    err.write("%d sessions, %d AI-assisted commits, %d other commits across "
              "%d projects, %d repos\n"
              % (totals.sessions, totals.ai_commits, totals.other_commits,
                 len(report.projects), len(report.repos)))
    return 0


def main(argv: Optional[List[str]] = None, out: Optional[IO[str]] = None,
         err: Optional[IO[str]] = None) -> int:
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    args = build_parser().parse_args(argv)
    redact = not args.no_redact
    if args.no_redact:
        err.write("agent-standup: --no-redact is set -- "
                  "secrets and home paths will NOT be masked\n")

    period = resolve_period(args.since, args.until, args.utc)
    if period is None:
        err.write("agent-standup: could not parse --since/--until\n")
        return 2
    start, until = period
    if start >= until:
        err.write("agent-standup: --since must be earlier than --until\n")
        return 2

    scan = ScanStats()
    refs = find_transcripts(project=args.project, stats=scan)
    stats = summarize_sessions(refs, start, until, stats=scan)
    _scan_report(scan, err)

    logs, repo_map, git_stats = [], {}, GitStats()
    if args.no_git:
        err.write("agent-standup: git axis skipped (--no-git)\n")
    elif stats.repo_candidates:
        logs, repo_map, git_stats = collect(stats.repo_candidates, start, until)
        for warning in git_stats.warnings:
            err.write("warning: %s\n" % warning)

    report = build_report(stats, logs, repo_map, git_stats, start, until, redact=redact)
    if not report.projects and not report.repos:
        err.write("agent-standup: nothing to report in the selected window\n")
        return 1

    return _emit(report, args, out, err)

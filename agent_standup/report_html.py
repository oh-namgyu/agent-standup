"""Assemble a merged :class:`merge.Report` into one self-contained HTML page.

No JavaScript, no external requests: every asset (CSS, favicon) is inlined.
A single :func:`esc` is the only path any dynamic string takes on its way
into the page, whether it lands in text content or inside an attribute, so
there is exactly one place that has to get HTML-escaping right.
"""

import html
from datetime import datetime, timezone
from typing import Dict, List, Optional

from . import __version__
from ._vendor.redactor import redact_text
from .gitlog import Commit
from .merge import ProjectEntry, Report, RepoEntry
from .report_md import humanize
from .template import CSS, FAVICON, TEMPLATE_HTML


def esc(value: object) -> str:
    """Escape for text and attribute use, dropping XML-illegal C0 controls."""
    text = "".join(char for char in str(value) if char >= " " and char != "\x7f")
    return html.escape(text, quote=True)


def _fill(template: str, values: Dict[str, str]) -> str:
    """Replace ``%%KEY%%`` placeholders in a single left-to-right pass."""
    out = []  # type: List[str]
    cursor = 0
    while True:
        start = template.find("%%", cursor)
        if start < 0:
            break
        end = template.find("%%", start + 2)
        if end < 0:
            break
        key = template[start + 2:end]
        if key in values:
            out.append(template[cursor:start])
            out.append(values[key])
            cursor = end + 2
        else:
            out.append(template[cursor:start + 2])
            cursor = start + 2
    out.append(template[cursor:])
    return "".join(out)


def _tile(value: object, caption: str, warn: bool = False, wide: bool = False) -> str:
    extra = (" warn" if warn else "") + (" span2" if wide else "")
    return ('<div class="tile%s"><b>%s</b><span>%s</span></div>'
            % (extra, esc(value), esc(caption)))


def tiles(report: Report) -> str:
    """Summary row. Skipped-item tiles appear only when something was skipped."""
    totals = report.totals
    period = "%s to %s" % (report.start.strftime("%Y-%m-%d %H:%M"),
                           report.until.strftime("%Y-%m-%d %H:%M"))
    parts = [
        _tile(totals.sessions, "sessions"),
        _tile(humanize(totals.active_seconds), "active time"),
        _tile(totals.ai_commits, "AI-assisted commits"),
        _tile(totals.other_commits, "other commits"),
        _tile(totals.files_changed, "files changed"),
        _tile(period, "period (UTC)", wide=True),
    ]
    if totals.repos_skipped:
        parts.append(_tile(totals.repos_skipped, "repos skipped", warn=True))
    if totals.files_skipped:
        parts.append(_tile(totals.files_skipped, "files skipped", warn=True))
    return "".join(parts)


def _project_card(entry: ProjectEntry) -> str:
    rows = [
        ("Sessions", entry.sessions), ("Active time", humanize(entry.active_seconds)),
        ("Turns", entry.turns), ("Tool calls", entry.tool_calls),
        ("Tokens in", "{:,}".format(entry.tokens.get("input", 0))),
        ("Tokens out", "{:,}".format(entry.tokens.get("output", 0))),
    ]
    dl = "".join("<dt>%s</dt><dd>%s</dd>" % (esc(key), esc(value)) for key, value in rows)
    note = ""
    if entry.related_repos:
        note = ('<p class="note">Related repos: %s</p>'
               % esc(", ".join(entry.related_repos)))
    return '<article class="card"><h3>%s</h3><dl>%s</dl>%s</article>' % (
        esc(entry.label), dl, note)


def _commit_item(commit: Commit, redact: bool) -> str:
    subject = redact_text(commit.subject) if redact else commit.subject
    stat = "+%d/-%d, %d file%s" % (commit.insertions, commit.deletions, commit.files_changed,
                                   "" if commit.files_changed == 1 else "s")
    return "<li>%s<span class=\"stat\">%s</span></li>" % (esc(subject), esc(stat))


def _repo_card(entry: RepoEntry, redact: bool) -> str:
    items = "".join(_commit_item(commit, redact) for commit in entry.ai_commits)
    other = ""
    if entry.other_count:
        other = ('<p class="other-line">%d other commit%s (+%d/-%d)</p>'
                % (entry.other_count, "" if entry.other_count == 1 else "s",
                   entry.other_insertions, entry.other_deletions))
    return ('<article class="card"><h3>%s</h3><ul class="commits">%s</ul>%s</article>'
           % (esc(entry.label), items, other))


def _section(title: str, cards_html: str) -> str:
    if not cards_html:
        return ""
    return ('<h2 class="section">%s</h2><section class="cards">%s</section>'
           % (esc(title), cards_html))


def crumbs(report: Report) -> str:
    items = ["%d projects" % len(report.projects), "%d repos" % len(report.repos)]
    return "".join("<span>%s</span>" % esc(item) for item in items)


def _crumb(text: str, warn: bool = False) -> str:
    cls = ' class="warn-badge"' if warn else ""
    return "<span%s>%s</span>" % (cls, esc(text))


def footer(report: Report, now: datetime) -> str:
    parts = [
        _crumb("Generated by agent-standup %s" % __version__),
        _crumb(now.strftime("%Y-%m-%d %H:%M UTC")),
        _crumb("redaction: on" if report.redacted else "redaction: OFF",
              warn=not report.redacted),
    ]
    if report.git_disabled:
        parts.append(_crumb("git axis disabled (%s)" % report.git_disabled_reason, warn=True))
    for warning in report.warnings:
        parts.append(_crumb("warning: %s" % warning, warn=True))
    return "".join(parts)


def render(report: Report, now: Optional[datetime] = None) -> str:
    """Build the complete, self-contained HTML report."""
    now = now or datetime.now(timezone.utc)
    title = "%s to %s" % (report.start.strftime("%Y-%m-%d"), report.until.strftime("%Y-%m-%d"))
    projects_html = "".join(_project_card(entry) for entry in report.projects)
    repos_html = "".join(_repo_card(entry, report.redacted) for entry in report.repos)
    return _fill(TEMPLATE_HTML, {
        "CSS": CSS, "FAVICON": FAVICON, "VERSION": esc(__version__), "TITLE": esc(title),
        "CRUMBS": crumbs(report), "TILES": tiles(report),
        "PROJECTS": _section("Projects", projects_html),
        "REPOS": _section("Repos", repos_html),
        "FOOTER": footer(report, now),
    })

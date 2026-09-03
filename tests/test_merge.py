"""merge.build_report tests: linking, filtering, sorting and totals."""

import unittest
from datetime import datetime, timezone

from agent_standup.gitlog import Commit, GitStats, RepoLog
from agent_standup.merge import build_report
from agent_standup.sessions import ProjectSummary, ScanStats, SessionStats

UTC = timezone.utc
START = datetime(2026, 1, 1, tzinfo=UTC)
UNTIL = datetime(2026, 1, 8, tzinfo=UTC)


def tokens(**over):
    base = dict.fromkeys(("input", "output", "cache_read", "cache_creation"), 0)
    base.update(over)
    return base


def project(cwd, sessions=1, active=60.0, turns=1, tools=0):
    return ProjectSummary(cwd=cwd, sessions=sessions, active_seconds=active,
                          turns=turns, tool_calls=tools, tokens=tokens())


def commit(subject, ai=False, ins=1, dels=0, files=1):
    return Commit(commit_hash="h" * 40, authored_at=0, subject=subject,
                 co_authors=(["Claude <noreply@anthropic.com>"] if ai else []),
                 insertions=ins, deletions=dels, files_changed=files, ai_assisted=ai)


class LinkingTestCase(unittest.TestCase):
    def test_project_links_to_its_repo_via_the_cwd_map(self):
        stats = SessionStats(projects={"/repo/a": project("/repo/a")})
        log = RepoLog(top="/repo/a", commits=[commit("add x")])
        report = build_report(stats, [log], {"/repo/a": "/repo/a"}, GitStats(), START, UNTIL)
        self.assertEqual(report.projects[0].related_repos, [report.repos[0].label])

    def test_project_with_no_matching_repo_has_no_related_repos(self):
        stats = SessionStats(projects={"/scratch/x": project("/scratch/x")})
        report = build_report(stats, [], {}, GitStats(), START, UNTIL)
        self.assertEqual(report.projects[0].related_repos, [])

    def test_repo_with_zero_in_window_commits_is_dropped(self):
        stats = SessionStats(projects={})
        log = RepoLog(top="/repo/empty", commits=[])
        report = build_report(stats, [log], {}, GitStats(), START, UNTIL)
        self.assertEqual(report.repos, [])


class SplitTestCase(unittest.TestCase):
    def test_ai_and_other_commits_are_split_and_summarized(self):
        log = RepoLog(top="/repo/a", commits=[
            commit("ai one", ai=True, ins=5, dels=1),
            commit("plain one", ai=False, ins=2, dels=3),
            commit("plain two", ai=False, ins=1, dels=1)])
        report = build_report(SessionStats(), [log], {}, GitStats(), START, UNTIL)
        entry = report.repos[0]
        self.assertEqual([c.subject for c in entry.ai_commits], ["ai one"])
        self.assertEqual(entry.other_count, 2)
        self.assertEqual(entry.other_insertions, 3)
        self.assertEqual(entry.other_deletions, 4)


class SortingTestCase(unittest.TestCase):
    def test_projects_sort_by_active_time_desc_then_label(self):
        stats = SessionStats(projects={
            "/a/small": project("/a/small", active=10.0),
            "/a/big": project("/a/big", active=100.0),
            "/a/tied-b": project("/a/tied-b", active=50.0),
            "/a/tied-a": project("/a/tied-a", active=50.0)})
        report = build_report(stats, [], {}, GitStats(), START, UNTIL, redact=False)
        self.assertEqual([p.cwd for p in report.projects],
                         ["/a/big", "/a/tied-a", "/a/tied-b", "/a/small"])

    def test_repos_sort_by_commit_count_desc_then_label(self):
        logs = [RepoLog(top="/r/quiet", commits=[commit("x")]),
               RepoLog(top="/r/loud", commits=[commit("a"), commit("b"), commit("c")])]
        report = build_report(SessionStats(), logs, {}, GitStats(), START, UNTIL, redact=False)
        self.assertEqual([r.top for r in report.repos], ["/r/loud", "/r/quiet"])


class TotalsTestCase(unittest.TestCase):
    def test_totals_aggregate_across_both_axes(self):
        stats = SessionStats(projects={
            "/a": project("/a", sessions=2, active=30.0),
            "/b": project("/b", sessions=1, active=15.0)})
        stats.scan = ScanStats(skipped=2)
        logs = [RepoLog(top="/r1", commits=[commit("a", ai=True, files=2),
                                           commit("b", ai=False, files=3)])]
        git_stats = GitStats()
        git_stats.skip("timeout")
        report = build_report(stats, logs, {}, git_stats, START, UNTIL)
        totals = report.totals
        self.assertEqual(totals.sessions, 3)
        self.assertEqual(totals.active_seconds, 45.0)
        self.assertEqual(totals.ai_commits, 1)
        self.assertEqual(totals.other_commits, 1)
        self.assertEqual(totals.files_changed, 5)
        self.assertEqual(totals.repos_skipped, 1)
        self.assertEqual(totals.files_skipped, 2)

    def test_git_disabled_state_is_carried_through(self):
        git_stats = GitStats()
        git_stats.disabled = True
        git_stats.disabled_reason = "git-missing"
        git_stats.warn("git binary not found; the git axis is disabled")
        report = build_report(SessionStats(), [], {}, git_stats, START, UNTIL)
        self.assertTrue(report.git_disabled)
        self.assertEqual(report.git_disabled_reason, "git-missing")
        self.assertEqual(len(report.warnings), 1)


if __name__ == "__main__":
    unittest.main()

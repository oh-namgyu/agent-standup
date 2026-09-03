"""gitlog tests against real temporary git repositories; no network, no shell."""

import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from agent_standup import gitlog
from agent_standup.gitlog import GitStats, collect_repo, discover, discover_repos, reset_git_probe

UTC = timezone.utc
FAR_PAST = datetime(2020, 1, 1, tzinfo=UTC)
FAR_FUTURE = datetime(2030, 1, 1, tzinfo=UTC)


def _run(args, cwd):
    subprocess.run(args, cwd=str(cwd), check=True, capture_output=True, text=True)


class RepoTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="agent-standup-git-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.addCleanup(reset_git_probe)
        reset_git_probe()

    def init_repo(self, name="repo"):
        top = self.tmp / name
        top.mkdir(parents=True)
        _run(["git", "init", "-q"], top)
        _run(["git", "config", "user.name", "Dev"], top)
        _run(["git", "config", "user.email", "d@example.invalid"], top)
        return top

    def commit(self, top, filename, content, when, message):
        path = top / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        _run(["git", "add", filename], top)
        env = dict(os.environ)
        env["GIT_AUTHOR_DATE"] = when
        env["GIT_COMMITTER_DATE"] = when
        subprocess.run(["git", "-c", "user.name=Dev", "-c", "user.email=d@example.invalid",
                       "commit", "-q", "-m", message],
                      cwd=str(top), check=True, capture_output=True, text=True, env=env)

    def merge_feature(self, top, when):
        _run(["git", "checkout", "-q", "-b", "feature"], top)
        self.commit(top, "feature.txt", "f\n", when, "feature commit")
        _run(["git", "checkout", "-q", "-"], top)
        env = dict(os.environ)
        env["GIT_AUTHOR_DATE"] = when
        env["GIT_COMMITTER_DATE"] = when
        subprocess.run(["git", "merge", "--no-ff", "-q", "-m", "merge feature", "feature"],
                      cwd=str(top), check=True, capture_output=True, text=True, env=env)

    def collect(self, top, start=FAR_PAST, until=FAR_FUTURE):
        stats = GitStats()
        return collect_repo(str(top), start, until, stats), stats

    def by_subject(self, log, subject):
        for commit in log.commits:
            if commit.subject == subject:
                return commit
        self.fail("no commit with subject %r among %r" % (subject, [c.subject for c in log.commits]))


class TrailerClassificationTestCase(RepoTestCase):
    def test_trailer_present_is_ai_assisted(self):
        top = self.init_repo()
        self.commit(top, "a.txt", "a\n", "2026-01-01T10:00:00+00:00",
                   "add a\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
        log, _stats = self.collect(top)
        commit = self.by_subject(log, "add a")
        self.assertTrue(commit.ai_assisted)
        self.assertEqual(commit.co_authors, ["Claude <noreply@anthropic.com>"])

    def test_multi_trailer_still_detects_claude(self):
        top = self.init_repo()
        self.commit(top, "a.txt", "a\n", "2026-01-01T10:00:00+00:00",
                   "add a\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n"
                   "Co-Authored-By: Jane <jane@example.invalid>")
        log, _stats = self.collect(top)
        commit = self.by_subject(log, "add a")
        self.assertTrue(commit.ai_assisted)
        self.assertEqual(len(commit.co_authors), 2)

    def test_trailer_value_with_display_name_and_noreply_address(self):
        top = self.init_repo()
        self.commit(top, "a.txt", "a\n", "2026-01-01T10:00:00+00:00",
                   "add a\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
        commit = self.by_subject(self.collect(top)[0], "add a")
        self.assertEqual(commit.co_authors, ["Claude <noreply@anthropic.com>"])

    def test_trailer_absent_is_other(self):
        top = self.init_repo()
        self.commit(top, "a.txt", "a\n", "2026-01-01T10:00:00+00:00", "plain commit")
        commit = self.by_subject(self.collect(top)[0], "plain commit")
        self.assertFalse(commit.ai_assisted)
        self.assertEqual(commit.co_authors, [])

    def test_body_prose_mention_is_not_a_real_trailer(self):
        top = self.init_repo()
        self.commit(top, "a.txt", "a\n", "2026-01-01T10:00:00+00:00",
                   "prose commit\n\nThis mentions Co-Authored-By: Claude in prose text.")
        commit = self.by_subject(self.collect(top)[0], "prose commit")
        self.assertFalse(commit.ai_assisted)
        self.assertEqual(commit.co_authors, [])


class LogShapeTestCase(RepoTestCase):
    def test_merge_commits_are_excluded(self):
        top = self.init_repo()
        self.commit(top, "a.txt", "a\n", "2026-01-01T10:00:00+00:00", "base commit")
        self.merge_feature(top, "2026-01-02T10:00:00+00:00")
        log, _stats = self.collect(top)
        subjects = [c.subject for c in log.commits]
        self.assertIn("base commit", subjects)
        self.assertIn("feature commit", subjects)
        self.assertNotIn("merge feature", subjects)

    def test_binary_file_numstat_is_tolerated(self):
        top = self.init_repo()
        self.commit(top, "bin.dat", b"\x00\x01\x02binary", "2026-01-01T10:00:00+00:00",
                   "binary commit")
        commit = self.by_subject(self.collect(top)[0], "binary commit")
        self.assertEqual(commit.insertions, 0)
        self.assertEqual(commit.deletions, 0)
        self.assertEqual(commit.files_changed, 1)

    def test_rename_is_tolerated_and_counted_as_one_file(self):
        top = self.init_repo()
        self.commit(top, "old.txt", "content\nmore\n", "2026-01-01T10:00:00+00:00", "add old")
        _run(["git", "mv", "old.txt", "new.txt"], top)
        env = dict(os.environ)
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = "2026-01-02T10:00:00+00:00"
        subprocess.run(["git", "commit", "-q", "-m", "rename old to new"], cwd=str(top),
                      check=True, capture_output=True, text=True, env=env)
        commit = self.by_subject(self.collect(top)[0], "rename old to new")
        self.assertEqual(commit.files_changed, 1)

    def test_period_boundary_is_start_inclusive_until_exclusive(self):
        top = self.init_repo()
        self.commit(top, "a.txt", "a\n", "2026-01-01T00:00:00+00:00", "at-start")
        self.commit(top, "b.txt", "b\n", "2026-01-02T00:00:00+00:00", "at-until")
        start = datetime(2026, 1, 1, tzinfo=UTC)
        until = datetime(2026, 1, 2, tzinfo=UTC)
        log, _stats = self.collect(top, start=start, until=until)
        subjects = [c.subject for c in log.commits]
        self.assertIn("at-start", subjects)
        self.assertNotIn("at-until", subjects)


class DiscoveryTestCase(RepoTestCase):
    def test_dedups_repos_by_toplevel_realpath(self):
        top = self.init_repo()
        (top / "sub").mkdir()
        stats = GitStats()
        tops = discover_repos([str(top), str(top / "sub")], stats)
        self.assertEqual(len(tops), 1)
        self.assertEqual(Path(tops[0]).resolve(), top.resolve())

    def test_non_repo_cwd_is_skipped_and_counted(self):
        outside = self.tmp / "not-a-repo"
        outside.mkdir()
        stats = GitStats()
        tops = discover_repos([str(outside)], stats)
        self.assertEqual(tops, [])
        self.assertEqual(stats.reasons.get("non-repo"), 1)

    def test_discover_map_points_every_candidate_at_its_canonical_top(self):
        top = self.init_repo()
        sub = top / "sub"
        sub.mkdir()
        stats = GitStats()
        tops, mapping = discover([str(top), str(sub)], stats)
        self.assertEqual(len(tops), 1)
        self.assertEqual(mapping[str(top)], mapping[str(sub)])
        self.assertEqual(mapping[str(top)], tops[0])

    def test_discover_map_omits_non_repo_candidates(self):
        outside = self.tmp / "not-a-repo"
        outside.mkdir()
        stats = GitStats()
        _tops, mapping = discover([str(outside)], stats)
        self.assertEqual(mapping, {})


class DegradationTestCase(RepoTestCase):
    def test_git_missing_disables_the_whole_axis_and_warns(self):
        with mock.patch("agent_standup.gitlog.subprocess.run",
                        side_effect=FileNotFoundError("git not found")):
            stats = GitStats()
            tops = discover_repos([str(self.tmp)], stats)
        self.assertEqual(tops, [])
        self.assertTrue(stats.disabled)
        self.assertEqual(stats.disabled_reason, "git-missing")
        self.assertTrue(stats.warnings)

    def test_timeout_during_collection_is_skipped_and_counted(self):
        top = self.init_repo()
        self.commit(top, "a.txt", "a\n", "2026-01-01T10:00:00+00:00", "add a")
        real_run = subprocess.run

        def fake_run(argv, **kwargs):
            if "log" in argv:
                raise subprocess.TimeoutExpired(cmd=argv, timeout=10)
            return real_run(argv, **kwargs)

        with mock.patch("agent_standup.gitlog.subprocess.run", side_effect=fake_run):
            log, stats = self.collect(top)
        self.assertEqual(log.commits, [])
        self.assertEqual(stats.reasons.get("timeout"), 1)


if __name__ == "__main__":
    unittest.main()

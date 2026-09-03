"""HTML renderer tests: escaping, self-containment, and hostile fixtures."""

import re
import unittest
from datetime import datetime, timezone

from agent_standup.gitlog import Commit, GitStats, RepoLog
from agent_standup.merge import build_report
from agent_standup.report_html import esc, render
from agent_standup.sessions import ProjectSummary, SessionStats

UTC = timezone.utc
START = datetime(2026, 1, 1, tzinfo=UTC)
UNTIL = datetime(2026, 1, 8, tzinfo=UTC)
NOW = datetime(2026, 1, 8, 12, 0, tzinfo=UTC)


def tokens():
    return dict.fromkeys(("input", "output", "cache_read", "cache_creation"), 0)


def commit(subject, ai=True):
    return Commit(commit_hash="a" * 40, authored_at=0, subject=subject,
                 co_authors=(["Claude <noreply@anthropic.com>"] if ai else []),
                 insertions=1, deletions=0, files_changed=1, ai_assisted=ai)


class EscTestCase(unittest.TestCase):
    def test_script_tags_are_escaped(self):
        self.assertNotIn("<script", esc("<script>alert(1)</script>"))
        self.assertIn("&lt;script&gt;", esc("<script>alert(1)</script>"))

    def test_quotes_are_escaped_for_attribute_safety(self):
        out = esc('say "hi" and \'bye\'')
        self.assertNotIn('"', out)

    def test_c0_control_characters_are_dropped(self):
        out = esc("a\x00b\x01c\x7f d")
        self.assertEqual(out, "abc d")

    def test_tab_and_newline_are_also_dropped(self):
        # Commit subjects are single-line; any embedded control char is noise.
        self.assertEqual(esc("a\tb\nc"), "abc")


class SelfContainmentTestCase(unittest.TestCase):
    def _report(self, extra_logs=()):
        stats = SessionStats(projects={"/a/proj": ProjectSummary(
            cwd="/a/proj", sessions=1, active_seconds=60.0, turns=1, tool_calls=0,
            tokens=tokens())})
        logs = [RepoLog(top="/a/proj", commits=[commit("normal commit")])] + list(extra_logs)
        return build_report(stats, logs, {"/a/proj": "/a/proj"}, GitStats(), START, UNTIL)

    def test_no_script_tag_anywhere(self):
        text = render(self._report(), now=NOW)
        self.assertNotIn("<script", text.lower())

    def test_no_external_asset_references(self):
        text = render(self._report(), now=NOW)
        for marker in ("http://", "https://", "<link rel=\"stylesheet\"", "src=\"http"):
            self.assertNotIn(marker, text)

    def test_favicon_and_css_are_inlined(self):
        text = render(self._report(), now=NOW)
        self.assertIn("data:image/png;base64,", text)
        self.assertIn("<style>", text)

    def test_is_well_formed_enough_to_find_matching_tags(self):
        text = render(self._report(), now=NOW)
        self.assertEqual(text.count("<article"), text.count("</article>"))
        self.assertEqual(len(re.findall(r"<html", text)), 1)


class HostileFixtureTestCase(unittest.TestCase):
    def test_hostile_commit_subject_cannot_inject_a_script_tag(self):
        stats = SessionStats()
        logs = [RepoLog(top="/x", commits=[commit('<script>alert(document.cookie)</script>')])]
        report = build_report(stats, logs, {}, GitStats(), START, UNTIL)
        text = render(report, now=NOW)
        self.assertNotIn("<script>alert", text)
        self.assertIn("&lt;script&gt;", text)

    def test_hostile_subject_with_quotes_cannot_break_out_of_an_attribute(self):
        stats = SessionStats()
        logs = [RepoLog(top="/x", commits=[commit('breakout" onmouseover="alert(1)')])]
        report = build_report(stats, logs, {}, GitStats(), START, UNTIL)
        text = render(report, now=NOW)
        self.assertNotIn('onmouseover="alert(1)"', text)

    def test_control_characters_in_subject_do_not_survive(self):
        stats = SessionStats()
        logs = [RepoLog(top="/x", commits=[commit("weird\x00\x01subject")])]
        report = build_report(stats, logs, {}, GitStats(), START, UNTIL)
        text = render(report, now=NOW)
        self.assertIn("weirdsubject", text)


if __name__ == "__main__":
    unittest.main()

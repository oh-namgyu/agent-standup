"""CLI tests: period parsing, DST safety, the fail-closed self-check, and one
full pipeline run against synthetic transcripts and a real temporary repo.
"""

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from agent_standup import cli
from agent_standup.cli import main, resolve_period, selfcheck

UTC = timezone.utc


class PeriodParsingTestCase(unittest.TestCase):
    def test_default_since_is_seven_days_before_now(self):
        start, until = resolve_period(cli.DEFAULT_SINCE, None, utc=True)
        self.assertAlmostEqual((until - start).total_seconds(), 7 * 86400, delta=2)

    def test_relative_days_form_variants(self):
        for value in ("7d", "7 days", "7day", "14D"):
            start, until = resolve_period(value, None, utc=True)
            days = 14 if "14" in value else 7
            self.assertAlmostEqual((until - start).total_seconds(), days * 86400, delta=2)

    def test_absolute_date_only_is_start_of_day(self):
        start, until = resolve_period("2026-01-01", "2026-01-02", utc=True)
        self.assertEqual(start, datetime(2026, 1, 1, tzinfo=UTC))
        self.assertEqual(until, datetime(2026, 1, 2, tzinfo=UTC))

    def test_explicit_offset_is_trusted_regardless_of_utc_flag(self):
        start, _until = resolve_period("2026-01-01T00:00:00+09:00", "2026-01-02", utc=False)
        self.assertEqual(start, datetime(2025, 12, 31, 15, 0, tzinfo=UTC))

    def test_unparseable_since_returns_none(self):
        self.assertIsNone(resolve_period("not-a-date", None, utc=True))

    def test_dst_safe_local_relative_period(self):
        if not hasattr(time, "tzset"):
            self.skipTest("time.tzset is POSIX-only")
        previous = os.environ.get("TZ")
        os.environ["TZ"] = "America/New_York"
        time.tzset()
        try:
            # "now" sits after the spring-forward transition; 7 days earlier
            # sits before it. A naive offset-reuse would be off by an hour.
            now_utc = datetime(2026, 3, 10, 13, 0, tzinfo=UTC)  # 09:00 EDT
            start = cli._parse_when("7d", False, now_utc)
            # 2026-03-03 09:00 local is EST (UTC-5) -> 14:00 UTC, not 13:00.
            self.assertEqual(start, datetime(2026, 3, 3, 14, 0, tzinfo=UTC))
        finally:
            if previous is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = previous
            time.tzset()


class SelfCheckTestCase(unittest.TestCase):
    def test_account_name_canary_is_caught(self):
        account = Path.home().name
        if len(account) < 3:
            self.skipTest("home account name too short for the redactor to target")
        self.assertIn("account name", selfcheck("leaked: %s" % account))

    def test_raw_home_prefix_canary_is_caught(self):
        self.assertTrue(any("home-path" in hit for hit in selfcheck("path: /Users/someone/x")))

    def test_unmasked_secret_pattern_canary_is_caught(self):
        # Runtime-assembled so no secret-shaped literal sits in this file.
        needle = "sk-" + "ant-" + ("x" * 20)
        hits = selfcheck("key: %s" % needle)
        self.assertTrue(any("anthropic-key" in hit for hit in hits))

    def test_clean_text_has_no_hits(self):
        self.assertEqual(selfcheck("nothing sensitive here"), [])


class SelfCheckGateTestCase(unittest.TestCase):
    """Proves the gate is wired into main(), not just callable standalone."""

    def _run_with_hostile_render(self, hostile_text, argv):
        from agent_standup.sessions import ProjectSummary, SessionStats
        out, err = io.StringIO(), io.StringIO()
        stats = SessionStats(projects={"/x": ProjectSummary(cwd="/x", sessions=1)})
        with mock.patch("agent_standup.cli.render_md", return_value=hostile_text), \
             mock.patch("agent_standup.cli.summarize_sessions", return_value=stats):
            code = main(argv, out=out, err=err)
        return code, out.getvalue(), err.getvalue()

    def test_account_name_leak_blocks_output(self):
        account = Path.home().name
        if len(account) < 3:
            self.skipTest("home account name too short for the redactor to target")
        code, stdout, stderr = self._run_with_hostile_render(
            "leaked %s" % account, ["--since=7d"])
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("refusing to emit", stderr)

    def test_secret_pattern_leak_blocks_output_and_writes_nothing(self):
        needle = "sk-" + "ant-" + ("y" * 20)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out.md"
            code, stdout, stderr = self._run_with_hostile_render(
                "key: %s" % needle, ["--since=7d", "-o", str(target)])
            self.assertEqual(code, 1)
            self.assertFalse(target.exists())
        self.assertIn("refusing to emit", stderr)

    def test_no_redact_skips_the_gate(self):
        account = Path.home().name
        if len(account) < 3:
            self.skipTest("home account name too short for the redactor to target")
        code, stdout, _err = self._run_with_hostile_render(
            "leaked %s" % account, ["--since=7d", "--no-redact"])
        self.assertEqual(code, 0)
        self.assertIn(account, stdout)


class FullPipelineTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="agent-standup-cli-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.config = self.tmp / "config"
        (self.config / "projects").mkdir(parents=True)
        self.previous_config = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.config)
        self.addCleanup(self._restore_config)

    def _restore_config(self):
        if self.previous_config is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self.previous_config

    def _write_transcript(self, project, cwd, events):
        folder = self.config / "projects" / project
        folder.mkdir(parents=True, exist_ok=True)
        for event in events:
            event.setdefault("cwd", cwd)
        path = folder / "s1.jsonl"
        path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")

    def _assistant(self, uuid, stamp, mid):
        return {"type": "assistant", "uuid": uuid, "timestamp": stamp,
               "message": {"role": "assistant", "id": mid,
                          "content": [{"type": "text", "text": "ok"}]}}

    def _git(self, args, cwd):
        subprocess.run(["git"] + args, cwd=str(cwd), check=True,
                      capture_output=True, text=True)

    def _init_repo(self, name):
        top = self.tmp / name
        top.mkdir()
        self._git(["init", "-q"], top)
        self._git(["config", "user.name", "Dev"], top)
        self._git(["config", "user.email", "d@example.invalid"], top)
        return top

    def _commit(self, top, filename, content, when, message):
        (top / filename).write_text(content, encoding="utf-8")
        self._git(["add", filename], top)
        env = dict(os.environ)
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = when
        subprocess.run(["git", "-c", "user.name=Dev", "-c", "user.email=d@example.invalid",
                       "commit", "-q", "-m", message], cwd=str(top), check=True,
                      capture_output=True, text=True, env=env)

    def test_report_answers_the_five_fixed_questions(self):
        repo_a = self._init_repo("repo-a")
        self._commit(repo_a, "x.txt", "x\n", "2026-01-03T10:00:00+00:00",
                    "add x\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
        quiet_dir = self.tmp / "not-a-repo"
        quiet_dir.mkdir()

        # A 10-minute gap keeps the busy project's active time inside the
        # 15-minute idle cutoff, so it actually registers as active time.
        self._write_transcript("-p-busy", str(repo_a), [
            self._assistant("a1", "2026-01-03T10:00:00Z", "m1"),
            self._assistant("a2", "2026-01-03T10:10:00Z", "m2")])
        self._write_transcript("-p-quiet", str(quiet_dir), [
            self._assistant("b1", "2026-01-03T10:00:00Z", "n1")])

        out, err = io.StringIO(), io.StringIO()
        code = main(["--since=2026-01-01", "--until=2026-01-08", "--utc"], out=out, err=err)
        text = out.getvalue()

        self.assertEqual(code, 0, err.getvalue())
        # Labels keep the last two path segments, so the temp directory's own
        # name rides along; match on the project-specific tail instead.
        busy_heading = re.search(r"^### .*-repo-a$", text, re.MULTILINE)
        quiet_heading = re.search(r"^### .*-not-a-repo$", text, re.MULTILINE)
        # Q1: active projects list.
        self.assertIsNotNone(busy_heading)
        self.assertIsNotNone(quiet_heading)
        # Q2: top project by active time (10m > 0m) leads the Projects section.
        self.assertLess(busy_heading.start(), quiet_heading.start())
        # Q3: AI-assisted commit count.
        self.assertIn("Commits: 1 AI-assisted, 0 other", text)
        # Q4: repo change size for the AI-assisted commit.
        self.assertIn("add x (+1/-0, 1 file)", text)
        # Q5: skipped-repos presence (the quiet project's cwd is not a repo).
        self.assertIn("1 repos skipped", text)

    def test_nothing_to_report_exits_one_and_writes_nothing(self):
        out, err = io.StringIO(), io.StringIO()
        code = main(["--since=7d", "--project=-nonexistent"], out=out, err=err)
        self.assertEqual(code, 1)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("nothing to report", err.getvalue())

    def test_bad_period_is_a_usage_error(self):
        out, err = io.StringIO(), io.StringIO()
        code = main(["--since=not-a-date"], out=out, err=err)
        self.assertEqual(code, 2)

    def test_since_after_until_is_a_usage_error(self):
        out, err = io.StringIO(), io.StringIO()
        code = main(["--since=2026-01-08", "--until=2026-01-01"], out=out, err=err)
        self.assertEqual(code, 2)

    def test_output_file_is_written_atomically_and_path_goes_to_stdout(self):
        self._write_transcript("-p-a", str(self.tmp / "somewhere"), [
            self._assistant("a1", "2026-01-03T10:00:00Z", "m1")])
        target = self.tmp / "report.md"
        out, err = io.StringIO(), io.StringIO()
        code = main(["--since=2026-01-01", "--until=2026-01-08", "--utc",
                    "--no-git", "-o", str(target)], out=out, err=err)
        self.assertEqual(code, 0, err.getvalue())
        self.assertEqual(out.getvalue().strip(), str(target.resolve()))
        self.assertIn("# Standup report", target.read_text(encoding="utf-8"))

    def test_html_without_output_flag_uses_the_default_filename(self):
        self._write_transcript("-p-a", str(self.tmp / "somewhere"), [
            self._assistant("a1", "2026-01-03T10:00:00Z", "m1")])
        cwd_before = os.getcwd()
        os.chdir(str(self.tmp))
        try:
            out, err = io.StringIO(), io.StringIO()
            code = main(["--since=2026-01-01", "--until=2026-01-08", "--utc",
                        "--no-git", "--html"], out=out, err=err)
            self.assertEqual(code, 0, err.getvalue())
            default_path = self.tmp / cli.DEFAULT_HTML_OUTPUT
            self.assertTrue(default_path.exists())
            self.assertIn("<html", default_path.read_text(encoding="utf-8"))
            self.assertIn(cli.DEFAULT_HTML_OUTPUT, err.getvalue())
        finally:
            os.chdir(cwd_before)


if __name__ == "__main__":
    unittest.main()

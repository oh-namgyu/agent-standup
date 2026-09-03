"""Session summarizer golden tests: every expected number is hand-computed from
the fixture directly above it. All windows are explicit UTC instants.
"""

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_standup.scanner import TranscriptRef
from agent_standup.sessions import NO_CWD, summarize_sessions

UTC = timezone.utc


def usage_block(inp=0, out=0, read=0, write=0):
    return {"input_tokens": inp, "output_tokens": out,
            "cache_read_input_tokens": read, "cache_creation_input_tokens": write}


def tool_use(identifier, name="Bash"):
    return {"type": "tool_use", "id": identifier, "name": name, "input": {"command": "ls"}}


def assistant(uuid, stamp, mid="m1", cwd=None, usage=None, blocks=None, **extra):
    message = {"role": "assistant",
               "content": [{"type": "text", "text": "ok"}] if blocks is None else blocks}
    if mid is not None:
        message["id"] = mid
    if usage is not None:
        message["usage"] = usage
    event = {"type": "assistant", "uuid": uuid, "timestamp": stamp, "message": message}
    if cwd is not None:
        event["cwd"] = cwd
    event.update(extra)
    return event


def user(uuid, stamp, content="hi", cwd=None, **extra):
    event = {"type": "user", "uuid": uuid, "timestamp": stamp,
             "message": {"role": "user", "content": content}}
    if cwd is not None:
        event["cwd"] = cwd
    event.update(extra)
    return event


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="agent-standup-sess-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.made = 0

    def write(self, events, project="-w-demo"):
        folder = self.tmp / project
        folder.mkdir(parents=True, exist_ok=True)
        self.made += 1
        path = folder / ("s%d.jsonl" % self.made)
        body = "".join(json.dumps(event) + "\n" for event in events)
        path.write_text(body, encoding="utf-8")
        return TranscriptRef(path=path, project=project, session_id=path.stem)

    def fold(self, *refs, start=None, until=None):
        start = start or datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
        until = until or datetime(2026, 1, 2, 0, 0, tzinfo=UTC)
        return summarize_sessions(list(refs), start, until)


class ActiveTimeTestCase(Base):
    def test_gap_under_15_minutes_is_included_but_over_is_excluded(self):
        # e1 -> e2: 14 min (840s, included). e2 -> e3: 16 min (960s, excluded -> 0).
        ref = self.write([
            assistant("a1", "2026-01-01T10:00:00Z", mid="m1"),
            assistant("a2", "2026-01-01T10:14:00Z", mid="m2"),
            assistant("a3", "2026-01-01T10:30:00Z", mid="m3")])
        stats = self.fold(ref)
        summary = list(stats.projects.values())[0]
        self.assertEqual(summary.active_seconds, 840.0)

    def test_parallel_sessions_double_count_active_time(self):
        session_a = self.write([assistant("a1", "2026-01-01T10:00:00Z", mid="m1"),
                                assistant("a2", "2026-01-01T10:10:00Z", mid="m2")])
        session_b = self.write([assistant("b1", "2026-01-01T10:00:00Z", mid="n1"),
                                assistant("b2", "2026-01-01T10:10:00Z", mid="n2")])
        stats = self.fold(session_a, session_b)
        self.assertEqual(len(stats.projects), 1)
        summary = list(stats.projects.values())[0]
        self.assertEqual(summary.sessions, 2)
        self.assertEqual(summary.active_seconds, 1200.0)  # 600 + 600, not deduped

    def test_reversed_timestamps_contribute_zero_not_negative(self):
        ref = self.write([assistant("a1", "2026-01-01T10:10:00Z", mid="m1"),
                          assistant("a2", "2026-01-01T10:00:00Z", mid="m2")])
        stats = self.fold(ref)
        summary = list(stats.projects.values())[0]
        self.assertEqual(summary.active_seconds, 0.0)

    def test_boundary_straddling_session_counts_only_the_windowed_portion(self):
        start = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
        until = datetime(2026, 1, 1, 11, 0, tzinfo=UTC)
        ref = self.write([
            user("u0", "2026-01-01T09:50:00Z"),                # before start
            user("u1", "2026-01-01T10:00:00Z"),                # == start, included
            user("u2", "2026-01-01T10:05:00Z"),                # included
            user("u3", "2026-01-01T11:00:00Z"),                # == until, excluded
            user("u4", "2026-01-01T11:10:00Z")])               # after until
        stats = self.fold(ref, start=start, until=until)
        summary = list(stats.projects.values())[0]
        self.assertEqual(summary.sessions, 1)
        self.assertEqual(summary.active_seconds, 300.0)  # only u1 -> u2, 5 minutes
        self.assertEqual(summary.turns, 2)  # only the two in-window user turns


class CwdAttributionTestCase(Base):
    def test_late_appearing_cwd_still_wins_attribution(self):
        ref = self.write([
            user("u1", "2026-01-01T10:00:00Z"),               # no cwd at all
            user("u2", "2026-01-01T10:01:00Z", cwd=""),        # blank cwd
            user("u3", "2026-01-01T10:02:00Z", cwd="/repo/a"),  # first valid
            user("u4", "2026-01-01T10:03:00Z", cwd="/repo/a")])
        stats = self.fold(ref)
        self.assertEqual(set(stats.projects), {"/repo/a"})
        self.assertEqual(stats.projects["/repo/a"].sessions, 1)

    def test_multi_repo_session_attributes_to_first_cwd_but_feeds_both_candidates(self):
        ref = self.write([
            user("u1", "2026-01-01T10:00:00Z", cwd="/repo/a"),
            user("u2", "2026-01-01T10:01:00Z", cwd="/repo/b")])
        stats = self.fold(ref)
        self.assertEqual(set(stats.projects), {"/repo/a"})
        self.assertEqual(stats.repo_candidates, {"/repo/a", "/repo/b"})

    def test_session_starting_outside_any_repo_still_attributes_and_feeds_candidates(self):
        # sessions.py has no notion of "is this a git repo" -- that's gitlog's job.
        # It just needs to attribute correctly and hand the raw cwd on for discovery.
        ref = self.write([user("u1", "2026-01-01T10:00:00Z", cwd="/scratch/outside")])
        stats = self.fold(ref)
        self.assertEqual(set(stats.projects), {"/scratch/outside"})
        self.assertEqual(stats.repo_candidates, {"/scratch/outside"})

    def test_no_valid_cwd_anywhere_falls_back_to_the_empty_key(self):
        ref = self.write([user("u1", "2026-01-01T10:00:00Z")])
        stats = self.fold(ref)
        self.assertEqual(set(stats.projects), {NO_CWD})
        self.assertEqual(stats.repo_candidates, set())


class TurnsToolsTokensTestCase(Base):
    def test_meta_and_sidechain_users_are_excluded_from_turns(self):
        ref = self.write([
            user("u1", "2026-01-01T10:00:00Z", isMeta=True),
            user("u2", "2026-01-01T10:01:00Z", isSidechain=True),
            user("u3", "2026-01-01T10:02:00Z", content="a real question")])
        stats = self.fold(ref)
        summary = list(stats.projects.values())[0]
        self.assertEqual(summary.turns, 1)

    def test_tool_result_only_user_event_is_not_a_turn(self):
        ref = self.write([user("u1", "2026-01-01T10:00:00Z",
                              content=[{"type": "tool_result", "tool_use_id": "t1"}])])
        stats = self.fold(ref)
        summary = list(stats.projects.values())[0]
        self.assertEqual(summary.turns, 0)

    def test_assistant_turns_dedup_by_message_id_and_skip_sidechain(self):
        ref = self.write([
            assistant("a1", "2026-01-01T10:00:00Z", mid="m1"),
            assistant("a2", "2026-01-01T10:00:01Z", mid="m1"),  # same id, streamed
            assistant("a3", "2026-01-01T10:00:02Z", mid="m2", isSidechain=True)])
        stats = self.fold(ref)
        summary = list(stats.projects.values())[0]
        self.assertEqual(summary.turns, 1)  # m1 once, m2 excluded (sidechain)

    def test_tool_calls_dedup_by_id(self):
        ref = self.write([assistant("a1", "2026-01-01T10:00:00Z",
                                    blocks=[tool_use("t1"), tool_use("t1")])])
        stats = self.fold(ref)
        summary = list(stats.projects.values())[0]
        self.assertEqual(summary.tool_calls, 1)

    def test_tokens_use_last_snapshot_per_message_id(self):
        ref = self.write([
            assistant("a1", "2026-01-01T10:00:00Z", mid="m1", usage=usage_block(10, 5)),
            assistant("a2", "2026-01-01T10:00:01Z", mid="m1", usage=usage_block(10, 9))])
        stats = self.fold(ref)
        summary = list(stats.projects.values())[0]
        self.assertEqual(summary.tokens["output"], 9)
        self.assertEqual(summary.tokens["input"], 10)


class ScanIntegrationTestCase(Base):
    def test_unreadable_transcript_is_skipped_not_fatal(self):
        ref = self.write([user("u1", "2026-01-01T10:00:00Z")])
        ref.path.chmod(0o000)
        try:
            if os.access(str(ref.path), os.R_OK):
                self.skipTest("file permissions are not enforced for this user")
            stats = self.fold(ref)
            self.assertEqual(stats.scan.skipped, 1)
            self.assertEqual(stats.scan.reasons, {"unreadable": 1})
            self.assertEqual(stats.projects, {})
        finally:
            ref.path.chmod(0o600)

    def test_empty_transcript_contributes_nothing(self):
        stats = self.fold(self.write([]))
        self.assertEqual(stats.scan.included, 1)
        self.assertEqual(stats.projects, {})


if __name__ == "__main__":
    unittest.main()

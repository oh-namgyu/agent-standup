"""Scanner tests against synthetic project trees; no real transcript is read."""

import json
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path

from agent_standup.scanner import (ENV_CONFIG_DIR, ScanStats, TranscriptRef, config_dir,
                                   find_transcripts, projects_dir, read_events)

LINE = {"type": "user", "uuid": "u1", "timestamp": "2026-01-01T10:00:00Z",
        "message": {"role": "user", "content": "hi"}}


class ScannerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="agent-standup-scan-"))
        self.addCleanup(self.cleanup)
        self.projects = self.tmp / "config" / "projects"
        self.projects.mkdir(parents=True)
        self.previous = os.environ.get(ENV_CONFIG_DIR)
        os.environ[ENV_CONFIG_DIR] = str(self.tmp / "config")

    def cleanup(self):
        if self.previous is None:
            os.environ.pop(ENV_CONFIG_DIR, None)
        else:
            os.environ[ENV_CONFIG_DIR] = self.previous
        for path in self.tmp.rglob("*"):
            try:
                path.chmod(0o700)
            except OSError:
                pass
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def seed(self, project, name, events=(LINE,), tail=None):
        folder = self.projects / project
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        text = "".join(json.dumps(event) + "\n" for event in events)
        path.write_text(text + (tail or ""), encoding="utf-8")
        return path

    def names(self, refs):
        return sorted(ref.path.name for ref in refs)

    def test_config_dir_follows_the_environment(self):
        self.assertEqual(config_dir(), self.tmp / "config")
        self.assertEqual(projects_dir(), self.projects)

    def test_finds_transcripts_one_level_deep(self):
        self.seed("-w-alpha", "a.jsonl")
        self.seed("-w-beta", "b.jsonl")
        stats = ScanStats()
        refs = find_transcripts(stats=stats)
        self.assertEqual(self.names(refs), ["a.jsonl", "b.jsonl"])
        self.assertEqual(stats.found, 2)
        self.assertEqual(sorted(ref.project for ref in refs), ["-w-alpha", "-w-beta"])
        self.assertEqual([ref.session_id for ref in refs if ref.path.name == "a.jsonl"], ["a"])

    def test_deeper_nesting_is_not_scanned(self):
        deep = self.projects / "-w-alpha" / "nested"
        deep.mkdir(parents=True)
        (deep / "deep.jsonl").write_text(json.dumps(LINE) + "\n", encoding="utf-8")
        self.seed("-w-alpha", "top.jsonl")
        self.assertEqual(self.names(find_transcripts()), ["top.jsonl"])

    def test_hidden_files_and_directories_are_skipped(self):
        self.seed("-w-alpha", "visible.jsonl")
        self.seed("-w-alpha", ".hidden.jsonl")
        self.seed(".hidden-project", "inside.jsonl")
        self.assertEqual(self.names(find_transcripts()), ["visible.jsonl"])

    def test_non_jsonl_files_are_ignored(self):
        self.seed("-w-alpha", "keep.jsonl")
        (self.projects / "-w-alpha" / "notes.txt").write_text("x", encoding="utf-8")
        (self.projects / "-w-alpha" / "data.json").write_text("{}", encoding="utf-8")
        self.assertEqual(self.names(find_transcripts()), ["keep.jsonl"])

    def test_symlinked_directory_is_not_followed(self):
        self.seed("-w-alpha", "real.jsonl")
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "sneaky.jsonl").write_text(json.dumps(LINE) + "\n", encoding="utf-8")
        os.symlink(str(outside), str(self.projects / "-w-linked"))
        self.assertEqual(self.names(find_transcripts()), ["real.jsonl"])

    def test_symlinked_file_is_skipped(self):
        real = self.seed("-w-alpha", "real.jsonl")
        os.symlink(str(real), str(self.projects / "-w-alpha" / "link.jsonl"))
        self.assertEqual(self.names(find_transcripts()), ["real.jsonl"])

    def test_project_filter_is_an_exact_match(self):
        self.seed("-w-alpha", "a.jsonl")
        self.seed("-w-alphabet", "b.jsonl")
        self.assertEqual(self.names(find_transcripts(project="-w-alpha")), ["a.jsonl"])
        self.assertEqual(find_transcripts(project="-w-nothing"), [])

    def test_missing_projects_directory_is_empty_not_an_error(self):
        shutil.rmtree(str(self.projects))
        stats = ScanStats()
        self.assertEqual(find_transcripts(stats=stats), [])
        self.assertEqual(stats.found, 0)

    def test_read_events_marks_unusable_lines(self):
        path = self.seed("-w-alpha", "a.jsonl", events=(LINE, LINE), tail="{not json\n")
        ref = TranscriptRef(path=path, project="-w-alpha", session_id="a")
        events = list(read_events(ref))
        self.assertEqual(len(events), 3)
        self.assertIsNone(events[-1])
        self.assertEqual(events[0]["uuid"], "u1")

    def test_unreadable_file_is_counted_as_skipped(self):
        self.seed("-w-alpha", "ok.jsonl")
        blocked = self.seed("-w-alpha", "blocked.jsonl")
        blocked.chmod(0o000)
        if os.access(str(blocked), os.R_OK):  # running as root defeats the test
            self.skipTest("file permissions are not enforced for this user")
        stats = ScanStats()
        refs = find_transcripts(stats=stats)
        self.assertEqual(stats.found, 2)

    def test_unreadable_project_directory_is_counted(self):
        self.seed("-w-alpha", "ok.jsonl")
        blocked = self.projects / "-w-blocked"
        blocked.mkdir()
        blocked.chmod(0o000)
        if os.access(str(blocked), os.R_OK):
            self.skipTest("directory permissions are not enforced for this user")
        stats = ScanStats()
        refs = find_transcripts(stats=stats)
        self.assertEqual(self.names(refs), ["ok.jsonl"])
        self.assertEqual(stats.skipped, 1)
        self.assertEqual(stats.reasons, {"unreadable-project-dir": 1})
        blocked.chmod(stat.S_IRWXU)

    def test_scan_stats_skip_accumulates_reasons(self):
        stats = ScanStats()
        stats.skip("unreadable")
        stats.skip("unreadable")
        stats.skip("parse-error")
        self.assertEqual(stats.skipped, 3)
        self.assertEqual(stats.reasons, {"unreadable": 2, "parse-error": 1})


if __name__ == "__main__":
    unittest.main()

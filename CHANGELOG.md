# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-09-04

Initial public release.

### Added

- `python -m agent_standup` builds one standup report for a time window from
  two sources side by side: Claude Code session transcripts under
  `~/.claude/projects` (or `CLAUDE_CONFIG_DIR`) and the git history of the
  repositories those sessions worked in.
- Session activity per project: sessions, active time (gaps over 15 minutes
  count as idle), turns, tool calls and tokens.
- Git axis: commits split into AI-assisted and other, using a real
  `Co-Authored-By` trailer containing "claude" as parsed by git (git 2.22+;
  on an older git, commits are collected but left unclassified with a
  warning). `--no-git` skips the axis.
- Markdown output by default; `--html` renders a self-contained page with no
  JavaScript. `-o` writes atomically and prints the written path.
- Period selection with `--since` / `--until` (relative or absolute,
  `[start, until)`), `--project` and `--utc`.
- Redaction on by default: labels shortened with the home prefix stripped,
  account names, email addresses, keys, tokens and home paths masked, and a
  fail-closed final scan that writes nothing if anything slipped through.
  `--no-redact` turns all of it off.
- No transcript message content in any report.
- Exit codes `0` (success), `1` (nothing to report, or the self-check refused)
  and `2` (usage or output error).
- Standard library only, Python 3.9+. The transcript parser and redactor are
  vendored from cc-replay at commit `04618de`.

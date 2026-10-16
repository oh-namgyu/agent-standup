# Contributing to agent-standup

Thanks for your interest! Bug reports and pull requests are welcome.

## Development setup

There is nothing to build and nothing to install at runtime.

```sh
python -m pip install pytest          # the only tool the suite needs
python -m pytest tests/ -q            # or: python -m unittest discover -s tests -v
python -m agent_standup --help
```

CI runs the suite on Python 3.9 and 3.12; please keep both working.

## Guidelines

- **Zero runtime dependencies.** Standard library plus the system `git`
  binary only. Do not add a third-party import.
- **No transcript content in reports.** Reports carry counts, labels and
  AI-assisted commit subjects only, and redaction stays on by default. Keep
  the fail-closed final scan in place; see [SECURITY.md](SECURITY.md).
- **Git stays read-only.** Invoke it with a fixed list-form argument vector,
  never a shell, and with a timeout.
- **Synthetic data only in tests.** Use fixtures and temporary git
  repositories; a test must never read a real transcript or real history.
- **Do not edit `agent_standup/_vendor/`.** Those files are copies from
  [cc-replay](https://github.com/oh-namgyu/cc-replay); fix them upstream and
  re-sync.
- Add a regression test for any bug fix.

## Pull requests

Keep PRs focused on one change, describe what changed and how you verified
it, and make sure the test suite is green before opening one.

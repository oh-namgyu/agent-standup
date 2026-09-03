# Security

## What this tool does

agent-standup runs entirely on your machine. It reads Claude Code transcript files, runs the
system `git` binary read-only against repositories it discovers from your session history, and
writes one Markdown or HTML report. Neither the CLI nor the generated report makes any network
request; the HTML report inlines all of its CSS and favicon and contains no JavaScript at all.

Transcripts are only ever read. The scan does not follow symlinks and does not descend past one
directory level under the projects directory. Git is invoked with a fixed, list-form argument
vector — never a shell — and every invocation is time-bounded.

## What ends up in the output

The report holds session activity counts (sessions, active time, turns, tool calls, token totals),
prettified project and repo labels, and **git commit subjects** for the commits it classifies as
AI-assisted. Commit subjects are free text your team already wrote into git history — they are not
transcript content, but they can still carry anything a commit message can carry: a file path, an
internal project name, occasionally a secret someone accidentally committed and later described in
the message. The report does not include commit bodies, diffs, or any transcript message text.

It still describes your working patterns: which projects you touch, how long you spend on each,
and how much of your recent git history was AI-assisted versus not. **Treat a generated report as
personal or team-sensitive, and review it before pasting it anywhere.**

## Redaction is best effort, and fail-closed as a backstop

Redaction is on by default. Project and repo labels are shortened to their last couple of path
segments with the home prefix stripped, and both labels and commit subjects are then run through a
redactor covering account names, email addresses, API keys and tokens, and home paths.

It is **pattern-based and not a guarantee**. A value in a shape the patterns do not cover can pass
through. As a backstop, before printing or writing anything the CLI re-scans the fully rendered
report one more time for the account name, a raw home-path prefix, or any of the redactor's own
secret patterns; if it finds one, it **writes nothing** and exits with an error instead of emitting
a partially-leaky report. This backstop only runs when redaction is on: `--no-redact` disables both
the labeling redaction and this final check, and shows raw paths and subjects.

## Reporting a problem

Please report security issues — in particular any way transcript content could reach the generated
report, a git argument that could be interpreted unsafely, or a shape of personal data that
redaction and the fail-closed check both miss — through GitHub issues on this repository. Describe
the shape of the input rather than pasting the real value.

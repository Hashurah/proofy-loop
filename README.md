# proofy-loop

An [Agent Skill](https://agentskills.io) that makes Claude Code and OpenAI Codex prove their fixes with real test runs, or admit they're unverified.

Coding agents often say "I've fixed the issue" when they have only read the code, run a different test from the one that failed, edited the code again after the last test run, or quietly skipped the failing test. proofy-loop closes those gaps. The agent may only finish with a verdict computed from recorded evidence:

- **VERIFIED**: the failure was reproduced, the fix was validated on the final code, and the diff was inspected.
- **UNVERIFIED**: anything less, with the exact reason.

## How it works

The skill walks the agent through five phases. A small helper script, `scripts/proofy.py`, records what actually ran and decides the verdict, so the agent's opinion of its own work is never an input.

| Phase | What happens | What counts as evidence |
|---|---|---|
| 1. Baseline | Run the failing check **before** changing code | Non-zero exit for the right reason (a command that couldn't even start doesn't count) |
| 2. Minimal change | Smallest fix for the root cause, written down | Recorded root cause; no weakened tests |
| 3. Validation | Re-run the same check, then the project's tests, type checker and build | Exit 0 on the **current** code (runs on older code are rejected as stale) |
| 4. Diff inspection | Read the full diff; automatic scan | No unresolved secrets, conflict markers, debug output, TODOs, skipped tests, removed assertions or deleted files |
| 5. Evidence report | `.proofy/evidence.md` and `.proofy/evidence.json` | Verdict plus every command, exit code, log and finding |

If verification is impossible (no database, missing credentials, no network, no tests), the agent stops, records the exact blocker, and reports UNVERIFIED instead of guessing.

## Install

**Claude Code**: clone into your personal skills folder, available in every project:

```sh
git clone https://github.com/Hashurah/proofy-loop.git ~/.claude/skills/proofy-loop
```

Or add it to one project at `.claude/skills/proofy-loop/`. Claude Code loads it automatically; check with `/skills`.

**OpenAI Codex**: clone into Codex's skills folder, for example:

```sh
git clone https://github.com/Hashurah/proofy-loop.git ~/.codex/skills/proofy-loop
```

The skill uses only plain instructions and shell commands (no hooks or platform-specific tools), so it should behave the same in Codex. It has not yet been tested in Codex, though; check your Codex version's documentation for its skills folder.

**Requirements**: git and the project's own toolchain. The helper needs Python 3.8+ (standard library only). Without Python, the skill has a manual mode with the same rules.

## Use

The skill triggers when the agent is fixing a bug or changing behaviour and is about to say it's done, or when you ask it to verify, prove or double-check a change. You can also ask for it directly: "use proofy-loop".

Under the hood, the agent runs commands like these:

```sh
proofy init --task "Fix tokens being rejected as expired"
proofy run baseline --label repro -- npm test -- auth     # must fail first
proofy note root-cause "exp is in seconds but was compared with milliseconds"
proofy run validate --label repro -- npm test -- auth     # same check, must pass
proofy run validate -- npx tsc --noEmit
proofy diff                                                # scan + full patch
proofy report                                              # exit 0 only if VERIFIED
```

`proofy` stands for `python3 <skill folder>/scripts/proofy.py`. Its state lives in `.proofy/` at the repository root, which is added to `.git/info/exclude` so it is never committed.

A finished task ends with a message like:

```
VERIFIED: tokens with second-based expiry are accepted again.

- Reproduced: `npm test -- auth` failed (exit 1, "expected true, got false")
- Changed: src/auth.js (seconds compared with milliseconds)
- Validated: `npm test -- auth` exit 0, `npx tsc --noEmit` exit 0
- Diff: 1 file, scan clean
- Still open: none

Full report: .proofy/evidence.md
```

## What it does not do

- It can't judge whether a test is a *good* test, only whether it ran, on which code, and what it reported. The agent is still told to read the diff and the output.
- The diff scan is pattern-based: it catches common secrets and leftovers, not everything. Intended findings can be acknowledged with a reason, which appears in the report.
- It doesn't replace CI or code review. It makes the agent's claim honest before either.

## Repository layout

```
SKILL.md                      instructions the agent follows
scripts/proofy.py             evidence recorder and verdict
references/stacks.md          test, type-check and build commands per ecosystem
references/report-template.md report format (Markdown and JSON)
tests/selftest.sh             end-to-end self-test
```

## Self-test

```sh
bash tests/selftest.sh python3
```

The self-test runs 26 scenarios in throwaway git repos, including a normal fix, editing after validation, a skipped test with debug output and a fake API key, an acknowledged finding, a blocker, a docs-only change, a run with zero tests and a folder with no git. It needs bash, git and Node.js. It passes on Windows 11 with Python 3.12 and 3.14; macOS and Linux have not been tested yet.

## License

MIT

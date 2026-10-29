---
name: proofy-loop
description: Zero-trust verification harness that makes a coding agent prove a fix or change with hard evidence before calling it done. It runs a 5-phase loop (reproduce the failure, make a minimal change, run real tests/builds/type checks, inspect the raw diff, write an evidence report) and returns VERIFIED or UNVERIFIED, never "should work". Use it whenever you fix a bug, change behaviour, implement a feature or refactor code in a repository and are about to say it is fixed, done, resolved or working. Also use it when the user asks you to prove, verify, double-check or show evidence for a change, mentions proofyLoop or proofy, or has been burned by "fixed" claims that weren't. Works in Claude Code and OpenAI Codex.
license: MIT
compatibility: Needs git and the project's own toolchain. The bundled scripts/proofy.py needs Python 3.8+ (standard library only); without Python, follow the manual mode at the end.
metadata:
  display-name: proofyLoop
  version: "1.0.0"
---

# proofyLoop: prove it or say it's unverified

A claim that code is fixed is worth exactly as much as the evidence behind it. Reading code and reasoning that it "should" work is a hypothesis, not a result. Agents (and people) routinely skip running the check, run a different check than the one that failed, weaken a test until it passes, or edit code after the last test run. This skill closes those gaps by making every completion claim rest on commands that actually ran, on the final code, with recorded exit codes.

## The completion rule

You may only describe work as finished in terms of the verdict this loop produces:

- **VERIFIED**: all five phases completed, the evidence report says VERIFIED.
- **UNVERIFIED**: anything else. Say what is missing and why.

Until the loop says VERIFIED, do not write "I've fixed the issue", "the bug is resolved", "changes implemented successfully", "this should work now", "tests pass" (without a run), or anything that implies a confirmed result. Those phrases are what the user reads as a guarantee. If you have not proven it, the honest statement is "I changed X; it is UNVERIFIED because Y."

This is not about pessimism. "UNVERIFIED: the test runner needs a database I can't start here" is a useful, trustworthy answer. "Fixed!" followed by a broken build is not.

## Scope

Use the loop for any change to code, configuration, build or test files. It is not needed for answering questions, explaining code, or writing prose that ships nowhere. For a documentation-only change, Phase 1 may be marked not applicable (see Phase 1), but the diff inspection and report still apply.

## Tooling

The helper `scripts/proofy.py` (next to this file) records evidence and computes the verdict, so the verdict comes from what ran, not from your own assessment. Locate it relative to this SKILL.md and invoke it with whichever Python exists: `python3`, `python`, or `py -3` on Windows. Below, `proofy` means that full invocation, for example:

```
python3 ~/.claude/skills/proofy-loop/scripts/proofy.py <command>
```

Run it from inside the repository. It keeps its state in `.proofy/` at the repo root and adds `.proofy/` to `.git/info/exclude` (local only, never committed). If no Python is available, use manual mode at the end of this file; the phases and the rules are the same.

Start every task with:

```
proofy init --task "<the user's request, in one sentence>"
proofy detect          # suggests the project's test/type/lint/build commands
```

## Phase 1: Baseline capture (reproduction)

Goal: watch the failure happen before changing anything, so you know the check you will use later actually detects the problem.

1. Identify the narrowest check that exposes the problem: an existing failing test, a test you write that encodes the expected behaviour, a CLI invocation, a type check, a build. Targeted beats broad: one test that fails for the right reason is better evidence than a whole suite. See `references/stacks.md` for how to run a single test in each ecosystem.
2. If you need a new test to reproduce the bug, writing it is part of this phase. Keep it focused on the behaviour the user described.
3. Run it through proofy, with a label you will reuse later:

   ```
   proofy run baseline --label repro -- <command>
   ```

   The baseline expects failure. Read the output: it should fail **for the reason in the bug report** (the right assertion, the right error), not because of a typo, a missing dependency or a wrong path. A check that fails for the wrong reason proves nothing; fix the check and run it again.
4. If the "failing" check passes, you have not reproduced the bug. Do not proceed to a fix you cannot test. Find a better reproduction, or ask the user for the missing detail.

When a behavioural baseline is genuinely impossible (a docs change, a pure rename the compiler will verify, a dependency bump with no reproducible symptom), record why:

```
proofy na baseline --reason "<specific reason>"
```

This is shown prominently in the report. Do not use it to avoid work you could do.

## Phase 2: Minimalist mutation

Goal: the smallest change that addresses the root cause, so the diff is reviewable and side effects are unlikely.

- Find the root cause before editing. Fixing where the symptom surfaces often leaves the cause in place. Record it:

  ```
  proofy note root-cause "<one or two sentences>"
  proofy note change "<what you changed and why>"   # optional, but helps the report
  ```

- Change only what the fix needs. No drive-by refactors, reformatting, renames, dependency changes or "while I'm here" improvements. If you notice another problem, record it as a risk (`proofy note risk "..."`) and mention it to the user instead of fixing it silently.
- Never make a check pass by weakening it: no deleting or skipping tests, loosening assertions, catching and ignoring the error, special-casing the test input, or changing expected values to match wrong output. Phase 4 looks for these patterns, and the report will show them.

## Phase 3: Execution validation

Goal: show, with real runs on the final code, that the problem is gone and nothing else broke.

1. Re-run the reproduction with the **same label**. It must now pass:

   ```
   proofy run validate --label repro -- <same command as the baseline>
   ```

2. Run the project's broader checks that the change could affect: the relevant test suite, the type checker (`tsc --noEmit`, `mypy`, `pyright`), the linter if CI enforces it, and the build. Use the project's own commands (package scripts, Makefile, CI config) over generic ones. `proofy detect` suggests them.

   ```
   proofy run validate -- npm test
   proofy run validate -- npx tsc --noEmit
   ```

3. Read each result. A pass that ran zero tests, skipped the relevant file or hit the wrong package is not evidence; proofy flags "zero tests" output, but you are the one who can tell whether the right tests ran.
4. If anything fails, go back to Phase 2. If you edit code after a validation run, that run is stale: proofy fingerprints the working tree, and the report rejects validations that ran on earlier code. Re-run.
5. Pre-existing failures unrelated to your change: show they also fail on the base commit (for example `git stash`, run, `git stash pop`), record that with `proofy note risk "..."`, and report them. Do not hide them, and do not "fix" them unasked.

## Phase 4: Diff safety inspection

Goal: confirm the change contains only what you meant to ship.

```
proofy diff
```

This writes the full patch to `.proofy/diff.patch` and scans the added lines for leaked secrets, merge-conflict markers, leftover debug output, new TODO/FIXME/HACK markers, skipped or focused tests, removed assertions and deleted files.

Then **read the whole patch yourself**. The scanner only knows patterns; you know the intent. Check that:

- every changed file belongs to the fix (no stray edits, generated files, lockfile churn or formatting noise);
- no debugging code, temporary logging, commented-out code or experiments remain;
- no credentials, tokens, personal paths or internal URLs were added;
- tests were added or strengthened, not weakened.

For each finding: fix it and run `proofy diff` again, or, if it is genuinely intended (a CLI's legitimate `print`, a test fixture that looks like a key), acknowledge it with a specific reason:

```
proofy ack F2 --reason "print() is the command's documented output, not debug code"
```

Acknowledgements appear in the report next to the finding, so the user can disagree.

## Phase 5: Structured evidence artifact

```
proofy note risk "<anything you could not verify, assumptions, follow-ups>"
proofy note approval "<actions only the user should take: deploys, migrations, secrets>"
proofy report
```

`report` writes `.proofy/evidence.md` (human-readable) and `.proofy/evidence.json` (machine-readable), prints the verdict, and exits 0 only for VERIFIED. The verdict is computed, not chosen. It is UNVERIFIED if any of these hold:

- no baseline was recorded (or marked not applicable), or it never reproduced the failure;
- the reproduction was never re-run as a validation;
- any validation's latest run failed, timed out, or ran on code that has since changed;
- the diff was not inspected, is empty, changed since inspection, or has unresolved findings;
- no root cause was recorded;
- a blocker was recorded.

The report's format is described in `references/report-template.md`.

## Graceful failure protocol

Sometimes verification is impossible here: the test runner needs a service you cannot start, dependencies fail to install, the network is blocked, credentials are missing, a command hangs, or the repository has no tests and you cannot reasonably add one.

When that happens:

1. Stop. Do not keep changing code you cannot test, and do not substitute a weaker check while presenting it as the real one.
2. Record the exact blocker, with the command and the error:

   ```
   proofy blocker --reason "npm test: ECONNREFUSED 127.0.0.1:5432 (tests need PostgreSQL; none is running)"
   ```

3. Still run `proofy diff` and `proofy report`, so the user gets the inspected diff and everything that was verified.
4. Tell the user the task is **UNVERIFIED**, the exact blocker, and the one thing that would unblock it.

If the blocker is later resolved, `proofy blocker --clear` and finish the loop.

## Your final message to the user

Lead with the verdict, then the evidence, in this shape:

```
UNVERIFIED (or VERIFIED): <one-sentence outcome>

- Reproduced: `<baseline command>` failed with <error> (exit N)
- Changed: <files> (<root cause in a few words>)
- Validated: `<command>` exit 0 (<N tests>), `<command>` exit 0
- Diff: <N files>, scan clean / <findings and how they were resolved>
- Still open: <risks, blockers, approvals needed>

Full report: .proofy/evidence.md
```

Quote real exit codes and real test counts from the runs. Never round "1 failed, 40 passed" up to "tests pass".

## Common ways verification gets faked (avoid all of them)

| Shortcut | Why it is not evidence |
|---|---|
| "I reviewed the code and it looks correct" | Reasoning is a hypothesis. Run it. |
| Running a different test than the one that failed | It may never have exercised the bug. Re-run the baseline label. |
| Editing after the last test run | The run proves an older version. proofy marks it stale. |
| Skipping, deleting or loosening a test | The check now proves nothing. Phase 4 flags it. |
| A pass with 0 tests collected | Nothing was tested. Check the runner's summary line. |
| Piping output through `tail`/`head` and trusting exit 0 | The pipe's exit code hides the test command's. Run the command directly, or check the counts. |
| "Tests pass" while some failed "for unrelated reasons" | Say exactly which failed and show they fail on the base commit too. |

## Platform notes

- **Claude Code**: install as a personal skill in `~/.claude/skills/proofy-loop/` or a project skill in `.claude/skills/proofy-loop/`.
- **OpenAI Codex**: install in Codex's skills folder (for example `~/.codex/skills/proofy-loop/`) or the repository's skills folder, per your Codex version's documentation.
- The skill relies only on reading these instructions and running shell commands, with no hooks or platform-specific tools, so it behaves the same in both. Approval prompts and sandboxing are the host agent's own; let them work normally, and treat a denied command as a blocker, not a pass.

## Manual mode (no Python)

Follow the same five phases and keep the evidence yourself:

1. Note `git rev-parse HEAD` as the base.
2. Run the baseline check; copy the exact command, exit code (`echo $?`, or `$LASTEXITCODE` in PowerShell) and the failure lines.
3. Make the change; record the root cause.
4. Re-run the same check and the broader checks after your **last** edit; record each command and exit code.
5. Run `git diff <base>` and `git status --porcelain` (for new files), read all of it, and note anything you found.
6. Write the report from `references/report-template.md` by hand, with the verdict decided by the same rules as in Phase 5.

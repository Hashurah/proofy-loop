# Evidence report format

`proofy report` generates this automatically. Use it as the template when writing a report by hand (manual mode).

## Markdown (`.proofy/evidence.md`)

```markdown
# Evidence Report: VERIFIED | UNVERIFIED

**Task:** <the user's request>
**Verdict:** VERIFIED | UNVERIFIED
**Generated:** <ISO timestamp>

## Why not verified            <- only when UNVERIFIED; one line per unmet rule
- Phase 3: 'npm test' did not pass on its latest run (exit 1).

## Environment
- <OS>, <runtime versions>, <git version>
- Repository: <path>, branch <name>, base <commit>

## Phase 1: Baseline (reproduction)
- `<command>` -> exit <N> (expected fail: reproduced), <duration>, log <path>
  <last lines of output showing the failure>
  (or) Not applicable: <specific reason>

## Phase 2: Change
- Root cause: <one or two sentences>
- Change: <what and why>
- <git diff --stat output>

## Phase 3: Validation
| # | Command | Expected | Exit | Result | Duration | Tests seen |
|---|---|---|---|---|---|---|
| 3 | `npm test -- auth` | pass | 0 | met | 4.1s | yes |

## Phase 4: Diff safety inspection
- Inspected <time> against <base>; <N> file(s) changed. Full patch: .proofy/diff.patch
- F1 [review] debug output in `src/cli.py`: `print(...)` -> acknowledged: <reason>

## Phase 5: Residual risks and open items
- Risk: <what was not verified, assumptions>
- Needs your approval: <deploys, migrations, secrets>

## Blockers                    <- only if any
- <exact command and error>
```

## JSON (`.proofy/evidence.json`)

```json
{
  "verdict": "VERIFIED",
  "reasons": [],
  "fingerprint": "3f9c0d2a7b1e4c55",
  "session": {
    "task": "Fix token expiry check",
    "base": "<commit sha>",
    "entries": [
      { "n": 1, "phase": "baseline", "label": "repro", "command": "npm test -- auth",
        "expect": "fail", "exit_code": 1, "met_expectation": true, "zero_tests": false,
        "duration_s": 3.2, "fingerprint": "...", "log": ".proofy/logs/001-baseline.log", "tail": "..." },
      { "n": 2, "phase": "validate", "label": "repro", "command": "npm test -- auth",
        "expect": "pass", "exit_code": 0, "met_expectation": true, "fingerprint": "..." }
    ],
    "notes": [{ "kind": "root-cause", "text": "..." }],
    "diff": { "files_changed": 1, "stat": "...", "findings": [] },
    "acks": {},
    "blockers": []
  }
}
```

`fingerprint` is a hash of every change relative to the base commit (tracked and untracked). A validation or diff inspection whose fingerprint differs from the current one ran on older code, and the verdict treats it as stale.

#!/usr/bin/env python3
"""proofy: evidence recorder for the proofy-loop verification skill.

Records what was actually run (commands, exit codes, output, working-tree
fingerprints), inspects the diff, and computes a verdict from that evidence
alone. The agent's opinion of its own work is never an input.

Standard library only; Python 3.8+. Works anywhere git and a shell exist.

    proofy.py init --task "what was asked"
    proofy.py detect
    proofy.py run baseline -- <command that should FAIL before the change>
    proofy.py na baseline --reason "why no behavioural baseline exists"
    proofy.py run validate -- <command that must PASS after the change>
    proofy.py note root-cause|change|risk|approval "text"
    proofy.py diff
    proofy.py ack F3 --reason "why this finding is intended"
    proofy.py blocker --reason "what stopped verification"
    proofy.py report            # exit 0 = VERIFIED, 2 = UNVERIFIED
    proofy.py status

State lives in .proofy/ at the repository root (added to .git/info/exclude,
which is local and never committed).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path

VERSION = "1.0.0"
STATE_DIR = ".proofy"
TAIL_LINES = 120

# ---------------------------------------------------------------- utilities


def now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def git(args, cwd, check=False):
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True)
    except FileNotFoundError:
        return None
    if check and r.returncode != 0:
        return None
    return r


def git_text(args, cwd):
    r = git(args, cwd)
    if r is None or r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", "replace").strip()


def repo_root() -> Path:
    top = git_text(["rev-parse", "--show-toplevel"], os.getcwd())
    return Path(top) if top else Path.cwd()


def state_path(root: Path) -> Path:
    return root / STATE_DIR / "session.json"


def load(root: Path) -> dict:
    p = state_path(root)
    if not p.exists():
        die("No proofy session here. Start one with: proofy.py init --task \"...\"")
    return json.loads(p.read_text(encoding="utf-8"))


def save(root: Path, s: dict) -> None:
    p = state_path(root)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def die(msg: str, code: int = 1):
    print(f"proofy: {msg}", file=sys.stderr)
    sys.exit(code)


def out(msg: str = "") -> None:
    # Plain ASCII-safe output: some Windows consoles choke on anything else.
    sys.stdout.write(msg.encode(sys.stdout.encoding or "utf-8", "replace").decode(sys.stdout.encoding or "utf-8") + "\n")


def fingerprint(root: Path, base: str | None) -> str | None:
    """Hash of every change relative to the session base: tracked (staged or
    not, committed since base) plus untracked files. Used to prove that
    validation and diff inspection happened on the final code."""
    if base is None:
        return None
    h = hashlib.sha256()
    d = git(["diff", "--binary", base, "--", ".", f":(exclude){STATE_DIR}"], root)
    if d is None or d.returncode != 0:
        return None
    h.update(d.stdout)
    for f in untracked(root):
        h.update(f.encode())
        try:
            h.update(hashlib.sha256((root / f).read_bytes()).digest())
        except OSError:
            h.update(b"<unreadable>")
    return h.hexdigest()[:16]


def untracked(root: Path):
    r = git(["ls-files", "--others", "--exclude-standard", "-z"], root)
    if r is None or r.returncode != 0:
        return []
    return sorted(f for f in r.stdout.decode("utf-8", "replace").split("\0") if f and not f.startswith(STATE_DIR + "/"))


# ------------------------------------------------------------------ init


def cmd_init(a):
    root = repo_root()
    p = state_path(root)
    if p.exists() and not a.force:
        die(f"A session already exists ({p}). Use --force to start over.")
    (root / STATE_DIR / "logs").mkdir(parents=True, exist_ok=True)
    head = git_text(["rev-parse", "HEAD"], root)
    excl = git_text(["rev-parse", "--git-path", "info/exclude"], root)
    if excl:
        ep = (root / excl) if not os.path.isabs(excl) else Path(excl)
        ep.parent.mkdir(parents=True, exist_ok=True)
        text = ep.read_text(encoding="utf-8") if ep.exists() else ""
        if f"{STATE_DIR}/" not in text.split():
            with ep.open("a", encoding="utf-8") as fh:
                fh.write(("" if text.endswith("\n") or not text else "\n") + f"{STATE_DIR}/\n")
    dirty = git_text(["status", "--porcelain"], root)
    s = {
        "version": VERSION,
        "task": a.task,
        "started": now(),
        "root": str(root),
        "base": head,
        "branch": git_text(["rev-parse", "--abbrev-ref", "HEAD"], root),
        "dirty_at_start": [l for l in (dirty or "").splitlines() if l and STATE_DIR not in l],
        "env": {
            "os": f"{platform.system()} {platform.release()}",
            "python": platform.python_version(),
            "git": git_text(["--version"], root),
        },
        "entries": [],
        "notes": [],
        "diff": None,
        "acks": {},
        "blockers": [],
    }
    save(root, s)
    out(f"proofy session started in {root}")
    if not head:
        out("WARNING: not a git repository (or no commits). Phase 4 diff inspection will be impossible,")
        out("         so the verdict cannot reach VERIFIED. Record this with: proofy.py blocker --reason ...")
    elif s["dirty_at_start"]:
        out(f"Note: {len(s['dirty_at_start'])} file(s) were already modified before this task; they count as part of the diff.")


# ----------------------------------------------------------------- detect

DETECTORS = []


def detector(fn):
    DETECTORS.append(fn)
    return fn


@detector
def _node(root):
    pj = root / "package.json"
    if not pj.exists():
        return []
    try:
        scripts = json.loads(pj.read_text(encoding="utf-8")).get("scripts", {})
    except (ValueError, OSError):
        scripts = {}
    pm = "npm"
    if (root / "pnpm-lock.yaml").exists():
        pm = "pnpm"
    elif (root / "yarn.lock").exists():
        pm = "yarn"
    elif (root / "bun.lockb").exists() or (root / "bun.lock").exists():
        pm = "bun"
    run = (lambda s: f"{pm} test") if pm == "npm" else (lambda s: f"{pm} {s}")
    found = []
    for name in ("test", "typecheck", "type-check", "lint", "build"):
        if name in scripts:
            found.append(("tests" if name == "test" else name, run(name) if name == "test" else f"{pm} run {name}" if pm == "npm" else f"{pm} {name}"))
    if (root / "tsconfig.json").exists() and not any(k in scripts for k in ("typecheck", "type-check")):
        found.append(("typecheck", "npx tsc --noEmit"))
    return found


@detector
def _python(root):
    markers = ["pyproject.toml", "setup.cfg", "setup.py", "pytest.ini", "tox.ini"]
    if not any((root / m).exists() for m in markers) and not list(root.glob("test*/**/*.py"))[:1]:
        return []
    found = [("tests", "python -m pytest -q")]
    cfg = "".join((root / m).read_text(encoding="utf-8", errors="replace") for m in markers if (root / m).exists())
    if "mypy" in cfg or (root / "mypy.ini").exists():
        found.append(("typecheck", "python -m mypy ."))
    if "pyright" in cfg:
        found.append(("typecheck", "pyright"))
    if "ruff" in cfg or (root / "ruff.toml").exists():
        found.append(("lint", "ruff check ."))
    return found


@detector
def _rust(root):
    return [("tests", "cargo test"), ("lint", "cargo clippy --all-targets -- -D warnings")] if (root / "Cargo.toml").exists() else []


@detector
def _go(root):
    return [("tests", "go test ./..."), ("vet", "go vet ./...")] if (root / "go.mod").exists() else []


@detector
def _jvm(root):
    if (root / "pom.xml").exists():
        return [("tests", "mvn -q test")]
    if (root / "build.gradle").exists() or (root / "build.gradle.kts").exists():
        return [("tests", "./gradlew test" if (root / "gradlew").exists() else "gradle test")]
    return []


@detector
def _dotnet(root):
    return [("tests", "dotnet test")] if list(root.glob("*.sln"))[:1] or list(root.glob("**/*.csproj"))[:1] else []


@detector
def _ruby_php_make(root):
    found = []
    if (root / "Gemfile").exists():
        found.append(("tests", "bundle exec rspec" if (root / "spec").exists() else "bundle exec rake test"))
    if (root / "composer.json").exists():
        found.append(("tests", "vendor/bin/phpunit"))
    mk = root / "Makefile"
    if mk.exists() and re.search(r"^test\s*:", mk.read_text(encoding="utf-8", errors="replace"), re.M):
        found.append(("tests", "make test"))
    return found


def cmd_detect(a):
    root = repo_root()
    found = [x for d in DETECTORS for x in d(root)]
    if not found:
        out("No known test/build tooling detected. Find the project's own commands (README, CI config, Makefile).")
        return
    out("Suggested validation commands (prefer a targeted test first, then these):")
    for kind, cmd in found:
        out(f"  {kind:10} {cmd}")
    ci = [p for p in (".github/workflows", ".gitlab-ci.yml", "azure-pipelines.yml", ".circleci") if (root / p).exists()]
    if ci:
        out(f"CI config found ({', '.join(ci)}): the commands CI runs are the strongest evidence; mirror them.")


# -------------------------------------------------------------------- run

NOT_STARTED = re.compile(
    r"is not recognized as an internal or external command|command not found|No such file or directory|"
    r"cannot find the path specified|The term '.*' is not recognized|No module named pytest|ModuleNotFoundError: No module named 'pytest'",
    re.I,
)

ZERO_TESTS = re.compile(
    r"no tests ran|collected 0 items|ran 0 tests|0 passing|no test files|no tests to run|"
    r"\b0 (tests?|specs?) (passed|run)|Tests:\s+0 total|testing: warning: no tests|No tests found|"
    r"^\s*[ℹ#]\s*tests 0\b",  # node --test summary: "ℹ tests 0" / "# tests 0"
    re.I | re.M,
)


def cmd_run(a):
    root = repo_root()
    s = load(root)
    if not a.command:
        die("Give the command after --, e.g.: proofy.py run validate -- npm test")
    command = " ".join(a.command)
    expect = a.expect or ("fail" if a.phase == "baseline" else "pass")
    label = a.label or command
    out(f"[proofy] {a.phase}: {command}  (expect {expect})")
    t0 = time.time()
    try:
        if a.shell:
            proc = subprocess.run([a.shell, "-c", command], cwd=os.getcwd(), capture_output=True, timeout=a.timeout)
        else:
            proc = subprocess.run(command, cwd=os.getcwd(), shell=True, capture_output=True, timeout=a.timeout)
        code = proc.returncode
        output = (proc.stdout + proc.stderr).decode("utf-8", "replace")
        timed_out = False
    except subprocess.TimeoutExpired as e:
        code = None
        output = ((e.stdout or b"") + (e.stderr or b"")).decode("utf-8", "replace") + f"\n[proofy] TIMED OUT after {a.timeout}s"
        timed_out = True
    dur = round(time.time() - t0, 2)
    n = len(s["entries"]) + 1
    log = root / STATE_DIR / "logs" / f"{n:03d}-{a.phase}.log"
    log.write_text(output, encoding="utf-8")
    lines = output.splitlines()
    for line in lines[-TAIL_LINES:]:
        out(line)
    if len(lines) > TAIL_LINES:
        out(f"[proofy] ... {len(lines) - TAIL_LINES} earlier lines in {log}")

    # A command that never started "fails" for the wrong reason; it reproduces nothing.
    not_started = (code in (126, 127, 9009)) or bool(NOT_STARTED.search(output[:2000]))
    if timed_out:
        met = False
    elif expect == "fail":
        met = code != 0 and not not_started
    elif expect == "pass":
        met = code == 0
    else:
        met = True
    entry = {
        "n": n,
        "phase": a.phase,
        "label": label,
        "command": command,
        "cwd": os.path.relpath(os.getcwd(), root),
        "expect": expect,
        "exit_code": code,
        "timed_out": timed_out,
        "met_expectation": met,
        "zero_tests": bool(ZERO_TESTS.search(output)),
        "not_started": not_started,
        "duration_s": dur,
        "at": now(),
        "fingerprint": fingerprint(root, s["base"]),
        "log": str(log.relative_to(root)),
        "log_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest()[:16],
        "tail": "\n".join(lines[-25:]),
    }
    s["entries"].append(entry)
    save(root, s)
    state = "TIMEOUT" if timed_out else f"exit {code}"
    warn = "  (WARNING: output suggests zero tests ran)" if entry["zero_tests"] else ""
    if not_started:
        warn = "  (the command itself could not start, so this proves nothing about the code; fix the command)"
    out(f"[proofy] recorded #{n} {a.phase}: {state}, expectation {'MET' if met else 'NOT MET'}{warn}")
    sys.exit(0 if met else 3)


def cmd_na(a):
    root = repo_root()
    s = load(root)
    if a.phase != "baseline":
        die("Only the baseline phase can be marked not applicable.")
    if len(a.reason.strip()) < 15:
        die("Give a real reason (what makes a failing reproduction impossible or meaningless here).")
    s["entries"].append({"n": len(s["entries"]) + 1, "phase": "baseline", "na": True, "reason": a.reason, "at": now(), "fingerprint": fingerprint(root, s["base"])})
    save(root, s)
    out("[proofy] baseline marked not applicable; this will be shown prominently in the report.")


def cmd_note(a):
    root = repo_root()
    s = load(root)
    s["notes"].append({"kind": a.kind, "text": a.text, "at": now()})
    save(root, s)
    out(f"[proofy] noted ({a.kind}).")


def cmd_blocker(a):
    root = repo_root()
    s = load(root)
    s["blockers"].append({"reason": a.reason, "at": now()})
    save(root, s)
    out("[proofy] blocker recorded. The verdict is now UNVERIFIED until it is cleared with: proofy.py blocker --clear")


def cmd_blocker_clear(root, s):
    s["blockers"] = []
    save(root, s)
    out("[proofy] blockers cleared.")


# ------------------------------------------------------------------- diff

BLOCK = "block"
REVIEW = "review"

SECRET_PATTERNS = [
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("API key (sk-...)", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("hard-coded credential", re.compile(r"(?i)\b(api[_-]?key|secret|passwd|password|token|auth)\b\s*[:=]\s*['\"][^'\"\s]{8,}['\"]")),
]
CONFLICT = re.compile(r"^(<{7}|>{7})( |$)|^={7}$")

DEBUG = [
    (re.compile(r"\.(js|jsx|ts|tsx|mjs|cjs|vue|svelte)$"), re.compile(r"\bconsole\.(log|debug|trace|dir)\(|\bdebugger\b"), "debug output"),
    (re.compile(r"\.py$"), re.compile(r"^\s*print\(|\bbreakpoint\(\)|pdb\.set_trace|\bipdb\b"), "debug output"),
    (re.compile(r"\.rs$"), re.compile(r"\bdbg!\(|^\s*println!\("), "debug output"),
    (re.compile(r"\.go$"), re.compile(r"^\s*fmt\.Print(ln|f)?\("), "debug output"),
    (re.compile(r"\.(java|kt)$"), re.compile(r"System\.(out|err)\.print|\.printStackTrace\("), "debug output"),
    (re.compile(r"\.php$"), re.compile(r"\b(var_dump|print_r|dd)\("), "debug output"),
    (re.compile(r"\.rb$"), re.compile(r"binding\.pry|\bbyebug\b"), "debug output"),
    (re.compile(r"\.cs$"), re.compile(r"Console\.Write(Line)?\(|Debug\.Write"), "debug output"),
]
TODO = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b")
TEST_FILE = re.compile(r"(^|/)(tests?|spec|__tests__)/|[._-](test|spec)\.[a-z]+$|_test\.(go|py|rb)$|^test_.*\.py$|/test_[^/]*\.py$|Tests?\.(cs|java|kt)$")
WEAKEN = re.compile(
    r"\.(only|skip)\(|\b(fit|fdescribe|xit|xdescribe|xtest)\(|@pytest\.mark\.(skip|xfail)|pytest\.skip\(|"
    r"@(Ignore|Disabled)\b|#\[ignore\]|\bt\.Skip(Now|f)?\(|\[Ignore\]|\[Fact\(Skip|unittest\.skip"
)
ASSERTION = re.compile(r"\bassert|\bexpect\(|\.should\b|\.to(Be|Equal|Throw|Match)|\bt\.(Error|Fatal)|Assert\.")


def parse_diff(text: str):
    """Yield (path, sign, line) for added '+' and removed '-' lines."""
    path = None
    for raw in text.splitlines():
        if raw.startswith("+++ "):
            p = raw[4:].strip()
            path = None if p == "/dev/null" else re.sub(r"^b/", "", p)
            continue
        if raw.startswith("--- "):
            p = raw[4:].strip()
            if path is None and p != "/dev/null":
                path = re.sub(r"^a/", "", p)
            continue
        if raw.startswith("diff --git"):
            path = None
            m = re.match(r"diff --git a/(.*) b/(.*)$", raw)
            if m:
                path = m.group(2)
            continue
        if raw.startswith("+") and path:
            yield path, "+", raw[1:]
        elif raw.startswith("-") and path:
            yield path, "-", raw[1:]


def cmd_diff(a):
    root = repo_root()
    s = load(root)
    base = a.base or s["base"]
    if not base:
        die("No git base to diff against (not a git repository?). Record a blocker: proofy.py blocker --reason ...", 2)
    r = git(["diff", "--no-color", "--no-ext-diff", base, "--", ".", f":(exclude){STATE_DIR}"], root)
    if r is None or r.returncode != 0:
        die(f"git diff failed: {(r.stderr.decode('utf-8', 'replace') if r else 'git not found').strip()}", 2)
    diff = r.stdout.decode("utf-8", "replace")
    stat = git_text(["diff", "--stat", base, "--", ".", f":(exclude){STATE_DIR}"], root) or ""
    names = git_text(["diff", "--name-status", base, "--", ".", f":(exclude){STATE_DIR}"], root) or ""
    new_files = untracked(root)

    findings = []

    def add(sev, kind, path, line, text):
        findings.append({"id": f"F{len(findings) + 1}", "severity": sev, "kind": kind, "file": path, "line": line, "text": text.strip()[:200]})

    added_asserts = removed_asserts = 0
    lines = list(parse_diff(diff))
    for f in new_files:
        p = root / f
        try:
            data = p.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8000] or len(data) > 2_000_000:
            add(REVIEW, "binary or large new file", f, None, f"{len(data)} bytes")
            continue
        for ln in data.decode("utf-8", "replace").splitlines():
            lines.append((f, "+", ln))

    counter = {}
    for path, sign, text in lines:
        counter[path] = counter.get(path, 0) + 1
        is_test = bool(TEST_FILE.search(path.replace("\\", "/")))
        if sign == "-":
            if is_test and ASSERTION.search(text):
                removed_asserts += 1
            continue
        if is_test and ASSERTION.search(text):
            added_asserts += 1
        for name, rx in SECRET_PATTERNS:
            if rx.search(text):
                add(BLOCK, f"possible secret: {name}", path, None, text)
        if CONFLICT.search(text):
            add(BLOCK, "merge conflict marker", path, None, text)
        for frx, rx, kind in DEBUG:
            if frx.search(path) and rx.search(text) and not is_test:
                add(REVIEW, kind, path, None, text)
        if TODO.search(text):
            add(REVIEW, "new TODO/FIXME/HACK", path, None, text)
        if is_test and WEAKEN.search(text):
            add(REVIEW, "test skipped/focused/disabled", path, None, text)
    if removed_asserts > added_asserts:
        add(REVIEW, "net assertions removed from tests", "(tests)", None, f"{removed_asserts} assertion lines removed, {added_asserts} added")
    for row in names.splitlines():
        parts = row.split("\t")
        if parts and parts[0].startswith("D"):
            add(REVIEW, "file deleted", parts[-1], None, "deleted")
    for e in s["entries"]:
        if e.get("phase") == "validate" and e.get("zero_tests") and e.get("met_expectation"):
            add(REVIEW, "validation run reported zero tests", e["label"], None, e["command"])

    s["diff"] = {
        "at": now(),
        "base": base,
        "fingerprint": fingerprint(root, s["base"]),
        "stat": stat,
        "name_status": names,
        "untracked": new_files,
        "files_changed": len(names.splitlines()) + len(new_files),
        "findings": findings,
    }
    (root / STATE_DIR / "diff.patch").write_text(diff, encoding="utf-8")
    save(root, s)

    out(stat or "(no tracked changes)")
    if new_files:
        out("New untracked files:\n  " + "\n  ".join(new_files))
    out(f"Full diff: {STATE_DIR}/diff.patch  -- read it; the scan below only catches known patterns.")
    if not findings:
        out("[proofy] diff scan: no findings.")
    for f in findings:
        out(f"  {f['id']} [{f['severity'].upper()}] {f['kind']}: {f['file']}: {f['text']}")
    if findings:
        out("[proofy] Fix each finding, or if it is intended: proofy.py ack <ID> --reason \"...\". Then rerun diff.")


def cmd_ack(a):
    root = repo_root()
    s = load(root)
    ids = {f["id"] for f in (s.get("diff") or {}).get("findings", [])}
    if a.id not in ids:
        die(f"No finding {a.id} in the latest diff inspection.")
    if len(a.reason.strip()) < 10:
        die("Give a specific reason this finding is intended.")
    s["acks"][a.id] = {"reason": a.reason, "at": now(), "diff_at": s["diff"]["at"]}
    save(root, s)
    out(f"[proofy] {a.id} acknowledged.")


# ----------------------------------------------------------------- verdict


def verdict(root: Path, s: dict):
    reasons = []
    current = fingerprint(root, s["base"])
    for b in s["blockers"]:
        reasons.append(f"Blocker: {b['reason']}")

    base_entries = [e for e in s["entries"] if e["phase"] == "baseline"]
    reproduced = [e for e in base_entries if not e.get("na") and e.get("met_expectation")]
    na = [e for e in base_entries if e.get("na")]
    if not base_entries:
        reasons.append("Phase 1: no baseline recorded (run the failing check before changing code, or mark it n/a with a reason).")
    elif not reproduced and not na:
        reasons.append("Phase 1: the baseline never reproduced the failure (every baseline run met none of its expectations).")

    val = [e for e in s["entries"] if e["phase"] == "validate"]
    latest = {}
    for e in val:
        latest[e["label"]] = e
    if not val:
        reasons.append("Phase 3: no validation command was run after the change.")
    for label, e in latest.items():
        if not e["met_expectation"]:
            reasons.append(f"Phase 3: '{label}' did not pass on its latest run (exit {e['exit_code']}{', timed out' if e.get('timed_out') else ''}).")
        elif current is not None and e.get("fingerprint") != current:
            reasons.append(f"Phase 3: code changed after '{label}' was validated; run it again on the final code.")
    for b in reproduced:
        v = latest.get(b["label"])
        if v is None:
            reasons.append(f"Phase 3: the reproducing check '{b['label']}' was never re-run as a validation (run it with the same --label).")

    d = s.get("diff")
    if current is None:
        reasons.append("Phase 4: no git history to diff against, so side effects cannot be ruled out.")
    elif not d:
        reasons.append("Phase 4: the diff was never inspected (proofy.py diff).")
    else:
        if d.get("fingerprint") != current:
            reasons.append("Phase 4: code changed after the diff inspection; run proofy.py diff again.")
        if d.get("files_changed", 0) == 0:
            reasons.append("Phase 2: the diff is empty, so nothing was changed.")
        for f in d["findings"]:
            ack = s["acks"].get(f["id"])
            if not ack or ack.get("diff_at") != d["at"]:
                reasons.append(f"Phase 4: unresolved finding {f['id']} [{f['severity']}] {f['kind']} in {f['file']}.")
    if not any(n["kind"] == "root-cause" for n in s["notes"]):
        reasons.append("Phase 2: no root-cause note recorded (proofy.py note root-cause \"...\").")
    return ("VERIFIED" if not reasons else "UNVERIFIED"), reasons, current


def fence(text: str) -> str:
    return "```\n" + (text.strip() or "(empty)") + "\n```"


def cmd_report(a):
    root = repo_root()
    s = load(root)
    v, reasons, current = verdict(root, s)
    notes = lambda k: [n["text"] for n in s["notes"] if n["kind"] == k]  # noqa: E731
    d = s.get("diff") or {}
    md = [f"# Evidence Report: {v}", ""]
    md += [f"**Task:** {s['task']}", f"**Verdict:** {v}", f"**Generated:** {now()} by proofy {VERSION}", ""]
    if reasons:
        md += ["## Why not verified", *[f"- {r}" for r in reasons], ""]
    md += ["## Environment", f"- {s['env']['os']}, Python {s['env']['python']}, {s['env'].get('git') or 'no git'}",
           f"- Repository: `{s['root']}`, branch `{s.get('branch')}`, base `{(s.get('base') or 'none')[:12]}`",
           f"- Working-tree fingerprint now: `{current}`", ""]
    md += ["## Phase 1: Baseline (reproduction)"]
    for e in [e for e in s["entries"] if e["phase"] == "baseline"]:
        if e.get("na"):
            md += [f"- **Not applicable:** {e['reason']}"]
        else:
            md += [f"- `{e['command']}` -> exit {e['exit_code']} (expected {e['expect']}: {'reproduced' if e['met_expectation'] else 'NOT reproduced'}), {e['duration_s']}s, log `{e['log']}`",
                   fence(e["tail"])]
    md += ["", "## Phase 2: Change"]
    md += [f"- Root cause: {t}" for t in notes("root-cause")] or ["- Root cause: (not recorded)"]
    md += [f"- Change: {t}" for t in notes("change")]
    md += [fence(d.get("stat", "(diff not inspected)"))]
    if d.get("untracked"):
        md += ["New files: " + ", ".join(f"`{f}`" for f in d["untracked"])]
    md += ["", "## Phase 3: Validation", "| # | Command | Expected | Exit | Result | Duration | Tests seen |", "|---|---|---|---|---|---|---|"]
    for e in [e for e in s["entries"] if e["phase"] == "validate"]:
        stale = " (stale)" if current is not None and e.get("fingerprint") != current else ""
        md += [f"| {e['n']} | `{e['command']}` | {e['expect']} | {'timeout' if e.get('timed_out') else e['exit_code']} | {'met' if e['met_expectation'] else 'NOT met'}{stale} | {e['duration_s']}s | {'none' if e.get('zero_tests') else 'yes'} |"]
    md += ["", "## Phase 4: Diff safety inspection"]
    if d:
        md += [f"- Inspected {d['at']} against `{d['base'][:12]}`; {d['files_changed']} file(s) changed. Full patch: `{STATE_DIR}/diff.patch`."]
        if not d["findings"]:
            md += ["- Scan findings: none (secrets, conflict markers, debug output, TODOs, skipped tests, removed assertions)."]
        for f in d["findings"]:
            ack = s["acks"].get(f["id"])
            md += [f"- {f['id']} [{f['severity']}] {f['kind']} in `{f['file']}`: `{f['text']}`" + (f" -> acknowledged: {ack['reason']}" if ack else " -> UNRESOLVED")]
    else:
        md += ["- Not performed."]
    md += ["", "## Phase 5: Residual risks and open items"]
    md += [f"- Risk: {t}" for t in notes("risk")] or ["- (none recorded)"]
    md += [f"- Needs your approval: {t}" for t in notes("approval")]
    if s["blockers"]:
        md += ["", "## Blockers", *[f"- {b['reason']}" for b in s["blockers"]]]
    text = "\n".join(md) + "\n"
    (root / STATE_DIR / "evidence.md").write_text(text, encoding="utf-8")
    report = {"verdict": v, "reasons": reasons, "fingerprint": current, "session": s}
    (root / STATE_DIR / "evidence.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    out(text if not a.quiet else f"{v}")
    out(f"[proofy] written: {STATE_DIR}/evidence.md and {STATE_DIR}/evidence.json")
    sys.exit(0 if v == "VERIFIED" else 2)


def cmd_status(a):
    root = repo_root()
    s = load(root)
    v, reasons, _ = verdict(root, s)
    out(f"Task: {s['task']}")
    for e in s["entries"]:
        out(f"  #{e['n']} {e['phase']}: " + (f"n/a ({e['reason']})" if e.get("na") else f"{e['command']} -> exit {e['exit_code']} ({'met' if e['met_expectation'] else 'NOT met'})"))
    out(f"Diff inspected: {'yes' if s.get('diff') else 'no'}; notes: {len(s['notes'])}; blockers: {len(s['blockers'])}")
    out(f"Current verdict: {v}")
    for r in reasons:
        out(f"  - {r}")


# -------------------------------------------------------------------- main


def main():
    p = argparse.ArgumentParser(prog="proofy", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=VERSION)
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("init", help="start a verification session")
    i.add_argument("--task", required=True)
    i.add_argument("--force", action="store_true")
    i.set_defaults(fn=cmd_init)

    sub.add_parser("detect", help="suggest validation commands for this repo").set_defaults(fn=cmd_detect)

    r = sub.add_parser("run", help="run and record a command")
    r.add_argument("phase", choices=["baseline", "validate"])
    r.add_argument("--expect", choices=["pass", "fail", "any"])
    r.add_argument("--label", help="name used to match a baseline check with its re-run (default: the command)")
    r.add_argument("--timeout", type=int, default=1800)
    r.add_argument("--shell", help="run through this shell with -c (e.g. bash); default is the system shell")
    r.set_defaults(fn=cmd_run)

    n = sub.add_parser("na", help="mark the baseline not applicable, with a reason")
    n.add_argument("phase", choices=["baseline"])
    n.add_argument("--reason", required=True)
    n.set_defaults(fn=cmd_na)

    t = sub.add_parser("note", help="record root cause, change summary, risk or approval item")
    t.add_argument("kind", choices=["root-cause", "change", "risk", "approval"])
    t.add_argument("text")
    t.set_defaults(fn=cmd_note)

    d = sub.add_parser("diff", help="inspect the diff since the session base")
    d.add_argument("--base")
    d.set_defaults(fn=cmd_diff)

    k = sub.add_parser("ack", help="acknowledge a diff finding as intended")
    k.add_argument("id")
    k.add_argument("--reason", required=True)
    k.set_defaults(fn=cmd_ack)

    b = sub.add_parser("blocker", help="record (or --clear) something that prevents verification")
    b.add_argument("--reason")
    b.add_argument("--clear", action="store_true")
    b.set_defaults(fn=lambda a: cmd_blocker_clear(repo_root(), load(repo_root())) if a.clear else (cmd_blocker(a) if a.reason else die("--reason is required")))

    rp = sub.add_parser("report", help="write the evidence report; exit 0 only if VERIFIED")
    rp.add_argument("--quiet", action="store_true")
    rp.set_defaults(fn=cmd_report)

    sub.add_parser("status", help="show recorded evidence and the current verdict").set_defaults(fn=cmd_status)

    # Everything after the first "--" is the command to run, verbatim, so its
    # own flags can never be mistaken for proofy's (or vice versa).
    argv = sys.argv[1:]
    command = []
    if "--" in argv:
        i = argv.index("--")
        argv, command = argv[:i], argv[i + 1 :]
    a = p.parse_args(argv)
    a.command = command
    a.fn(a)


if __name__ == "__main__":
    main()

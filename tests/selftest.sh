#!/usr/bin/env bash
# Self-test for scripts/proofy.py: 26 end-to-end scenarios in throwaway git repos.
# Needs bash, git and node. Usage: bash tests/selftest.sh [python3|python|"py -3"]
PY="${1:-python3}"; HERE="$(cd "$(dirname "$0")" && pwd)"; P="$PY $HERE/../scripts/proofy.py"
pass=0; fail=0
check() { if [ "$2" = "$3" ]; then echo "  ok   $1"; pass=$((pass+1)); else echo "  FAIL $1 (got $2, want $3)"; fail=$((fail+1)); fi; }
fresh() {
  W="${TMPDIR:-/tmp}/proofy-selftest-$1"; rm -rf "$W"; mkdir -p "$W/src" "$W/test"; cd "$W"
  git init -q -b main; git config user.email t@t; git config user.name t; git config core.autocrlf false
  echo '{"name":"t","type":"module","scripts":{"test":"node --test"}}' > package.json
  printf 'export function isValid(token, now = Date.now()) {\n  return token.exp > now; // exp is in seconds\n}\n' > src/auth.js
  cat > test/auth.test.js <<'T'
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isValid } from '../src/auth.js';
test('accepts a token expiring in an hour', () => {
  assert.equal(isValid({ exp: Math.floor(Date.now() / 1000) + 3600 }), true);
});
T
  git add -A; git commit -qm init
  $P init --task "Fix tokens rejected as expired" >/dev/null
}
fix() { sed -i 's#token.exp > now#token.exp * 1000 > now#' src/auth.js; }
verdict() { $P report --quiet >/dev/null 2>&1; echo $?; }

echo "A. full loop -> VERIFIED"
fresh a
$P run baseline --label repro -- node --test test/auth.test.js >/dev/null; check "baseline reproduces (exit 0 from proofy)" $? 0
fix; $P note root-cause "exp is seconds, compared with milliseconds" >/dev/null
$P run validate --label repro -- node --test test/auth.test.js >/dev/null; check "repro now passes" $? 0
$P run validate -- npm test >/dev/null
$P diff >/dev/null
check "verdict VERIFIED" "$(verdict)" 0
grep -q "# Evidence Report: VERIFIED" .proofy/evidence.md; check "evidence.md says VERIFIED" $? 0
git status --porcelain | grep -q proofy; check ".proofy is not seen by git" $? 1

echo "B. edit after validation -> stale"
echo "// later tweak" >> src/auth.js
check "verdict UNVERIFIED after a later edit" "$(verdict)" 2
grep -q "code changed after 'repro' was validated" .proofy/evidence.md; check "report names the stale validation" $? 0

echo "C. baseline command that cannot start is not a reproduction"
fresh c
$P run baseline --label repro -- nonexistent-runner test >/dev/null; check "proofy says expectation not met" $? 3
fix; $P note root-cause "x" >/dev/null; $P run validate --label repro -- nonexistent-runner test >/dev/null
$P diff >/dev/null
check "verdict UNVERIFIED" "$(verdict)" 2
grep -q "never reproduced" .proofy/evidence.md; check "report says not reproduced" $? 0

echo "D. weakened test, debug output and a secret are caught"
fresh d
$P run baseline --label repro -- node --test test/auth.test.js >/dev/null
sed -i "s#^test('accepts#test.skip('accepts#" test/auth.test.js
printf 'export function isValid(token, now = Date.now()) {\n  console.log("dbg", token);\n  const api_key = "sk-abcdefghijklmnopqrstuvwxyz123456";\n  return token.exp > now;\n}\n' > src/auth.js
$P note root-cause "x" >/dev/null
$P run validate --label repro -- node --test test/auth.test.js >/dev/null
out=$($P diff)
echo "$out" | grep -q "test skipped/focused/disabled"; check "skipped test flagged" $? 0
echo "$out" | grep -q "debug output"; check "console.log flagged" $? 0
echo "$out" | grep -q "possible secret"; check "secret flagged" $? 0
check "verdict UNVERIFIED with unresolved findings" "$(verdict)" 2

echo "E. acknowledged intended finding -> VERIFIED"
fresh e
$P run baseline --label repro -- node --test test/auth.test.js >/dev/null
fix; printf '// TODO: rotate keys in a later task\n' >> src/auth.js
$P note root-cause "exp is seconds" >/dev/null
$P run validate --label repro -- node --test test/auth.test.js >/dev/null
$P diff >/dev/null
$P ack F1 --reason "tracked follow-up the user asked to defer" >/dev/null
check "verdict VERIFIED after a reasoned ack" "$(verdict)" 0
grep -q "acknowledged: tracked follow-up" .proofy/evidence.md; check "ack shown in report" $? 0

echo "F. blocker -> UNVERIFIED, then cleared"
$P blocker --reason "integration tests need PostgreSQL; none running" >/dev/null
check "verdict UNVERIFIED with blocker" "$(verdict)" 2
$P blocker --clear >/dev/null
check "verdict VERIFIED after clearing" "$(verdict)" 0

echo "G. reproduction never re-run as validation"
fresh g
$P run baseline --label repro -- node --test test/auth.test.js >/dev/null
fix; $P note root-cause "x" >/dev/null
$P run validate -- npm test >/dev/null
$P diff >/dev/null
check "verdict UNVERIFIED" "$(verdict)" 2
grep -q "was never re-run as a validation" .proofy/evidence.md; check "report explains" $? 0

echo "H. baseline n/a needs a reason; docs change still verifiable"
fresh h
$P na baseline --reason "short" >/dev/null 2>&1; check "too-short reason rejected" $? 1
fix; git commit -qam "code already fixed"; $P init --force --task "Add a README" >/dev/null
$P na baseline --reason "README wording change only; no runtime behaviour to reproduce" >/dev/null
printf "# Docs
" > README.md
$P note root-cause "README missing usage section" >/dev/null
$P run validate -- npm test >/dev/null
$P diff >/dev/null
check "verdict VERIFIED with n/a baseline" "$(verdict)" 0
grep -q "Not applicable" .proofy/evidence.md; check "n/a shown in report" $? 0

echo "I. zero-test run is flagged"
fresh i
$P run baseline --label repro -- node --test test/auth.test.js >/dev/null
fix; $P note root-cause "x" >/dev/null
$P run validate --label repro -- node --test test/auth.test.js >/dev/null
mkdir -p empty; $P run validate --label empty -- "cd empty && node --test" >/dev/null
$P diff | grep -q "zero tests"; check "zero-test validation flagged" $? 0

echo "J. not a git repo"
W="${TMPDIR:-/tmp}/proofy-selftest-j"; rm -rf "$W"; mkdir -p "$W"; cd "$W"
$P init --task "x" | grep -q "not a git repository"; check "warns at init" $? 0
check "verdict UNVERIFIED without git" "$(verdict)" 2

echo "--- $pass passed, $fail failed ($($PY --version))"

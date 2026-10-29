# Running checks per stack

Use the project's own commands first (package.json scripts, Makefile, CI workflow files). These are fallbacks and the syntax for running a single test, which is the best baseline.

| Stack | Whole suite | One test (good baseline) | Type check / static | Build |
|---|---|---|---|---|
| Node + Vitest | `npx vitest run` | `npx vitest run path/to/file.test.ts -t "name"` | `npx tsc --noEmit` | `npm run build` |
| Node + Jest | `npx jest` | `npx jest path/to/file.test.ts -t "name"` | `npx tsc --noEmit` | `npm run build` |
| Node built-in runner | `node --test` | `node --test --test-name-pattern="name" test/file.test.js` | `npx tsc --noEmit` | - |
| Playwright | `npx playwright test` | `npx playwright test file.spec.ts -g "name"` | - | - |
| Python + pytest | `python -m pytest -q` | `python -m pytest tests/test_x.py::test_name -q` | `python -m mypy .` / `pyright` | `python -m build` |
| Python unittest | `python -m unittest` | `python -m unittest tests.test_x.TestCase.test_name` | `python -m mypy .` | - |
| Rust | `cargo test` | `cargo test test_name -- --exact` | `cargo clippy --all-targets -- -D warnings` | `cargo build` |
| Go | `go test ./...` | `go test ./pkg/x -run '^TestName$'` | `go vet ./...` | `go build ./...` |
| Java (Maven) | `mvn -q test` | `mvn -q test -Dtest=ClassName#method` | (compile is part of test) | `mvn -q package` |
| Java/Kotlin (Gradle) | `./gradlew test` | `./gradlew test --tests 'pkg.Class.method'` | - | `./gradlew build` |
| .NET | `dotnet test` | `dotnet test --filter "FullyQualifiedName~Name"` | `dotnet build -warnaserror` | `dotnet build` |
| Ruby (RSpec) | `bundle exec rspec` | `bundle exec rspec spec/x_spec.rb:42` | - | - |
| PHP (PHPUnit) | `vendor/bin/phpunit` | `vendor/bin/phpunit --filter testName` | `vendor/bin/phpstan analyse` | - |

## Reading results

- Look at the runner's summary line (`N passed, M failed`, `ok`/`FAIL`, `test result:`), not only the exit code.
- "0 tests", "no tests ran", "collected 0 items" or "no test files" means nothing was checked: fix the filter or path.
- Avoid `cmd | tail` in recorded runs: on most shells the exit code becomes `tail`'s. Run the command directly; proofy already keeps the full log and shows the end of it.
- Windows: commands go through `cmd.exe` by default. For bash syntax, pass `--shell bash` to `proofy run` (Git Bash), or write PowerShell as `powershell -NoProfile -Command "..."`.

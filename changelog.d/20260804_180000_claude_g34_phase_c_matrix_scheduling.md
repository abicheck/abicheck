<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **A compiler profile now schedules its own `check-project.yml` check cell**
  (G34 Phase C). Two axes were previously fixed for a whole run regardless of
  what each profile declared, and together they blocked a real
  GCC/Clang/MSVC matrix from running through the shared reusable workflow at
  all. Every check cell ran on a hardcoded `ubuntu-latest`, so an `os:
  windows` profile could not be checked natively; and dependency
  provisioning came from one workflow-level `install-deps` boolean, so a
  GCC-profile cell and a Clang-profile cell in the same run could not each
  install a matching toolchain. Both are now derived per cell at plan time:
  `abicheck project plan` emits `runs_on` (from the profile's `os:`) and
  `dependency_source` (from a new `profiles.<id>.dependency_source:`) on
  every `run-plan.json` check, `check-project.yml` schedules on
  `matrix.runs_on`, and `check-target` forwards `dependency-source` to the
  root Action's existing input of that name. `os:` accepts
  `linux`/`windows`/`macos` (or `darwin`) and passes a GitHub-hosted runner
  label such as `ubuntu-24.04` through verbatim, since it was a free-form,
  never-consulted string before this change. **Nothing existing moves:** a
  profile with no `os:` resolves to `ubuntu-latest` — where every cell
  already ran — and an undeclared `dependency_source:` leaves the caller's
  workflow-level input, and in turn the legacy `install-deps` boolean,
  deciding exactly as before. The one behaviour change is that `os:` is
  load-bearing now, so a value naming no schedulable platform (`os: freebsd`)
  fails `project validate` and `project plan` rather than being silently
  ignored and scheduled on Linux — a cell run on the wrong platform reports
  success having gated the wrong thing. An actual native `windows-latest`
  lane exercising a real MSVC profile end to end remains separate, still-open
  work; this lands the scheduling mechanism, not a validated fixture project.

### Fixed

- **The Action's unset `dependency-source` default is now OS-aware.** It
  resolved to `conda-forge` on every platform, but every conda-forge source
  is explicitly unsupported on Windows (pixi's `native-toolchain*` features
  don't cover win-64) and the Action hard-fails them there — so a Windows
  runner that set neither `dependency-source` nor `install-deps` exited 1
  before reaching any analysis. An unset value on a Windows runner now
  resolves to `system`, which is what that error message already told users
  to pick and what `install-deps.sh`'s own Windows branch supports
  (warn-and-continue, matching the "toolchain is pre-installed on the image"
  story a Windows lane has anyway). An explicitly requested conda-forge
  source still errors rather than being silently rewritten, and no existing
  consumer can regress since the path this replaces was an unconditional
  failure. Reached for real by the per-profile scheduling above, which lets
  an `os: windows` profile run a native check cell for the first time.

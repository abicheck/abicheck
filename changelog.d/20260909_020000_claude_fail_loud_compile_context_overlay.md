<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`action/run.sh`'s `add_compile_context_flags()` silently continued after
  a failed compile-context config-overlay synthesis.** The script has no
  `set -e`, and the helper's interpreter invocation (`python3
  "$_COMPILE_CONTEXT_HELPER_PY" ...`) wasn't checked for exit status —
  a failure fell through to the unconditional `CMD+=(--config ...)`,
  running `compare`/`dump` with an empty or missing overlay instead of the
  requested `ast-frontend`/`gcc-*`/`sysroot`/`nostdinc`/`lang` compile
  context (CodeRabbit review). Now checks `PIPESTATUS[1]` and the overlay
  file's presence, and fails the step loud (`::error::` + exit 1) instead
  of silently degrading. Also switched the hardcoded `python3` in this one
  call site to `${_PY_BIN:-python3}`, the canonically-resolved interpreter
  every other Python invocation in this file already uses.

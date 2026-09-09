### Security

- **Action: release-topology/compile-context config overlay generation now
  runs through the isolated interpreter** — `add_release_topology_config_flags()`
  and `add_compile_context_flags()` in `action/run.sh` launched a bare
  `python3` from the checked-out repository's own working directory to
  synthesize their `--config` overlay, instead of the resolved, isolated
  `$_PY_BIN`/`$_PY_SAFE_DIR` interpreter (with `PYTHONPATH` cleared) every
  other inline Python invocation in this file already uses. On a runner
  where only `python` (not `python3`) is on `PATH` this simply failed to
  find an interpreter; more seriously, on a `pull_request`-triggered
  workflow running against a fork's branch, a committed `sitecustomize.py`
  (or similar Python startup hook) in the checkout could execute during
  that bare interpreter's own startup, before this script's body ever
  runs. Both call sites now route through the same isolation mechanism the
  merge helper they call into already uses.

### Fixed

- **Action: a Windows-qualified `build-config` path used to be
  misclassified as relative** — the config-overlay merge helper's
  `base_source` absolutization used a POSIX-only `!= /*` test, so a native
  Windows drive path (`C:\...`), UNC path (`\\server\share\...`), or
  root-relative path (`\foo`) was wrongly treated as relative and got a
  `$PWD/` prefix prepended, producing a malformed path inside the isolated
  Python subprocess. It now reuses the existing `_is_path_already_qualified`
  helper, the same one `$_PY_BIN` canonicalization and `_report_query`'s
  path anchoring already rely on.

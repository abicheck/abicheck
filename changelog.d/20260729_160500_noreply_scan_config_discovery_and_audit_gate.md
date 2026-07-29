### Fixed

- **`scan --against`'s project-config resolution now matches
  `resolve_compile_context`'s own discovery precedence** (explicit
  `--config` > `--sources` tree root > cwd-upward) instead of only walking
  upward from the current directory — a `scan --against --sources DIR` run
  outside `DIR` could otherwise resolve its `compile:` block from one
  `.abicheck.yml` and its scope/suppression settings from a different one.
- **A plain one-build `scan` (no `--against`) no longer attempts project-
  config resolution at all** — every field it resolves is comparison-only,
  so a malformed *auto-discovered* config could previously fail an
  unrelated audit scan outright.
- **A malformed auto-discovered config now warns and falls back**, matching
  `merge_compile_config`'s own established convention for the `compile:`
  block, instead of failing the run — only a config the user explicitly
  bound to (or `merge_compile_config`'s own sources-root check) is
  fail-loud.
- **`ScanRequest` (Python API) gained `collapse_versioned_symbols`** — the
  CLI already threaded a config-resolved `collapse_versioned_symbols`
  through `run_scan_core`, but the typed Python API had no field for it, so
  a library caller couldn't request the same ICU-style version-suffix
  handling `scan --against --config` gets automatically.

(Codex review, PR #657.)

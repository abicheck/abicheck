### Security

- **The composite Action's "Generate assurance-overlay config" step no
  longer writes its output to a predictable, in-checkout path.** A
  malicious PR could commit a symlink at
  `check-target-assurance-config.yml` pointing anywhere the runner user
  can write, and the step's `open(path, "w")` would follow it and
  overwrite the target with attacker-influenced YAML. The output now goes
  to a private, freshly `mktemp`-created file under `$RUNNER_TEMP` — a
  runtime-random name nothing in the untrusted checkout could have
  pre-planted a symlink at — forwarded to the internal analysis step as
  the overlay step's own `config-path` output. Independent of the earlier
  `sitecustomize.py` startup-isolation fix, which addresses a different
  vulnerability (code execution during interpreter startup, not where the
  write lands).
- **The same step no longer launders an auto-discovered `.abicheck.yml`
  into a trusted, explicit `--config`.** With `analysis-assurance-complete`
  enabled and no explicit `build-config`, the step used to copy the
  PR-controlled, auto-discovered project config into the overlay verbatim
  — including `build.query`/`compile.compiler`, both of which select
  code or an executable to run — and hand it to the nested analysis step
  as an explicit `--config`, which the engine trusts to execute either. A
  PR that merely declares `checks[].analysis.assurance: complete` (which
  `analysis-assurance-complete: true` does automatically whenever no
  build-config is named) could reach this path. `build.query`/
  `compile.compiler`/`build.compile_db` are now stripped and
  `resource_limits.max_bundle_facts_decode_nodes` is capped (never
  raised) via a new shared implementation
  (`abicheck.action_config_overlay`) also now used by `action/run.sh`'s
  own equivalent compile-context/release-topology overlay merges, so the
  two call sites can't independently drift on this trust boundary again.
- **A relative `compile.include_dirs` entry in the base config now
  resolves correctly after the overlay relocates it.** Writing the
  (explicit or auto-discovered) base config unchanged into the new
  `$RUNNER_TEMP`-located overlay file broke any config-relative
  `compile.include_dirs` entry — the nested CLI resolved it against the
  temp file's own directory instead of the real project root. Every such
  entry is now rewritten to an absolute path anchored at the original
  config's own project root before the overlay is written.
- **The step's base config document (discovered or explicit) is now
  validated against the real `BuildConfig` schema before any
  stripping/overlay merge runs.** A schema-invalid value in a field the
  step goes on to strip anyway (`build.query: 7`, `compile.compiler: []`,
  `build.compile_db: false`) was previously silently deleted as part of
  ordinary stripping, before the nested analysis step's own CLI ever got a
  chance to parse and reject it — turning a config a direct `compare
  --config <file>` invocation would refuse outright into a
  silently-accepted run purely because `analysis-assurance-complete` was
  enabled. Fixed via a new shared `abicheck.action_config_overlay.
  validate_base_config`, called by both this step and `action/run.sh`'s
  own equivalent merge before either one's stripping/overlay logic runs,
  so the two call sites can't independently drift on what counts as a
  valid base document.
- **A wrong-typed `resource_limits.max_bundle_facts_decode_nodes` is now
  left untouched instead of being silently replaced with `null`.**
  `strip_untrusted_execution_keys` used to coerce a non-integer value to
  `None` before resolving the capped budget, which read that `None` right
  back and then overwrote the original (wrong-typed) value with a literal
  `null` — printing a "capped to the conservative default" warning that
  described nothing that actually happened. A non-integer value is now
  left exactly as written, so the real `BuildConfig` schema error surfaces
  downstream instead.

### Documentation

- `docs/use/github-action.md` now names `.abicheck.yml`'s
  `assurance.require_complete: true` (rather than the retired
  `require-complete-analysis` root Action input) as what enables scan
  exit 1 under an incomplete-analysis gate.

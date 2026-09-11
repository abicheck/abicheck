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

### Documentation

- `docs/use/github-action.md` now names `.abicheck.yml`'s
  `assurance.require_complete: true` (rather than the retired
  `require-complete-analysis` root Action input) as what enables scan
  exit 1 under an incomplete-analysis gate.

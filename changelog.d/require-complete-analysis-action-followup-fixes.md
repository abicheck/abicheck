### Fixed

- **The composite Action's assurance-floor enforcement no longer silently
  no-ops when `assurance.require_complete: true` is set only through
  `.abicheck.yml`.** `action/run.sh`'s `_assurance_gated()` now reads the
  JSON report's own `analysis_assurance_exit_contribution` field instead of
  the retired `require-complete-analysis` Action input, so the belt-and-
  suspenders exit-1 floor still fires independently of `fail-on-breaking`/
  `fail-on-api-break`.
- **`check-project.yml`'s `checks[].analysis.assurance: complete` is
  enforced again.** `actions/check-target/action.yml` gained an
  `analysis-assurance-complete` input that merges an
  `assurance: {require_complete: true}` fragment into the `build-config`
  the internal analysis step reads — the config-overlay replacement for the
  retired `require-complete-analysis` input/flag.
- **A `--contract` compare's persisted receipt no longer disagrees with
  itself under `assurance.require_complete: true`.**
  `contract_context.with_resolved_gate()` now accepts the resolved
  `require_complete_analysis` value, so `effective_config_fields["gate.
  require_complete_analysis"]` and `contract_context.evaluation_context.
  resolved_config.gate.require_complete_analysis` agree.
- **A bundle (`kind: bundle`) project check declaring
  `checks[].analysis.assurance: complete` no longer ends the whole
  `check-project.yml` run in a hard CLI usage error.** The directory/
  package release fan-out has no single `analysis_assurance` result to
  gate on, so `_reject_set_input_flags` unconditionally rejects
  `require_complete_analysis=true` for it — `project_targets.py`'s
  `_check_issues` now rejects this combination at run-plan generation
  time instead, with an actionable message, mirroring how
  `allow_new_target` is already rejected for a bundle check.
- **The composite Action's assurance-overlay generation step no longer
  silently discards a malformed `assurance:` block in a project's
  `build-config`.** A wrong-typed value (`assurance: false`, a bare
  string, or a list) — every one already rejected by normal `BuildConfig`
  ingestion — now fails the "Generate assurance-overlay config" step
  loudly instead of being replaced with `{}`; only a missing key or an
  explicit `null` is treated as "safe to overlay onto".
- **A `--contract` compare's persisted receipt now records *why*
  `gate.require_complete_analysis` was enabled, not just that it was.**
  `field_provenance["gate.require_complete_analysis"]` was still omitted
  after the fix above; `contract_context.with_resolved_gate()` now stamps
  a `project_config`-layer provenance entry itself whenever an explicit
  `require_complete_analysis=True` is passed — the field has no CLI
  override and no pack route, so `True` can only ever have come from
  `.abicheck.yml`'s `assurance.require_complete`.
- **`analysis_assurance_exit_contribution`'s packaged and published JSON
  Schema descriptions (`compare_report.schema.json`,
  `aggregate_report.schema.json`, and their `docs/reference/schemas/v1/`
  mirrors) no longer describe the trigger as the retired
  `--require-complete-analysis` CLI flag.** They now name the live
  `.abicheck.yml` `assurance.require_complete: true` config key, matching
  the earlier fix to the no-baseline notice text above.
- **The assurance-overlay step no longer mangles an explicit Windows-style
  `build-config` path.** On a Windows/Git-Bash runner, `C:/...`, `C:\...`,
  and a UNC `\\server\share\...` path all start with something other than
  `/`, so the step's POSIX-only `case "$BASE_CONFIG" in /*) ... esac`
  qualification test misclassified every one of them as relative and
  prefixed it with `$PWD`, producing a malformed, doubled path. Fixed by
  duplicating `action/run.sh`'s own `_is_path_already_qualified()` helper
  logic exactly (the established convention this Action step already
  follows for shared shell snippets, rather than sourcing `run.sh`).
- **The assurance-overlay step no longer leaks `require_complete` into an
  unrelated top-level config key when the base document uses a YAML
  anchor/alias.** `yaml.safe_load()` resolves an anchor/alias pair (e.g.
  `assurance: &shared {}` / `gate: *shared`) to the SAME dict object for
  both keys; the step used to mutate that object in place
  (`assurance["require_complete"] = True`), which also mutated the
  aliased sibling key, and `yaml.safe_dump()` then preserved the alias,
  writing `require_complete` under both keys in the generated overlay —
  the nested CLI then rejected the overlay outright for the unrelated key
  even though the original config was valid. Fixed by copying the
  `assurance` mapping before adding `require_complete` to it.
- **The assurance-overlay step no longer mangles its own Windows-style
  temporary output path either.** The same POSIX-only leading-slash
  qualification bug fixed above for `BASE_CONFIG` also affected the
  step's own freshly-`mktemp`-created overlay file path: on a Windows/
  Git-Bash runner, `mktemp` can return a path already inherited from
  `$RUNNER_TEMP` in drive-qualified form (e.g. `D:/a/_temp/tmp.XXXXXX`),
  which the same POSIX-only test misclassified as relative and prefixed
  with `$PWD`, breaking every assurance-enabled `check-target` invocation
  on Windows while generating the overlay. Fixed by reusing the same
  `_is_path_already_qualified()` helper for this second call site instead
  of a second copy of the qualification logic.
- **The assurance-overlay step no longer unconditionally discards a
  discovered `build.compile_db` that would have resolved to a real
  file.** Unlike `action/run.sh`'s own equivalent compile-context overlay
  merge (which keeps a demonstrably-resolving discovered `compile_db`),
  this step used to strip the field outright whenever `build-config` was
  omitted, believing it had no `--sources` root to validate the glob
  against — but the step's own `sources` Action input already supplies
  exactly that root. The resolution check (now shared between both
  callers as `abicheck.action_config_overlay.
  discovered_compile_db_resolves`, so they cannot independently drift) was
  also hardened while unifying it: it now requires the matched path's own
  *resolved* location to stay contained within the sources root, closing
  a path-traversal/symlink-escape gap a plain `match.is_file()` check
  alone did not catch (confirmed empirically: `Path.glob()` happily
  matches — and `is_file()` happily confirms — a pattern containing `..`
  components or one that walks through a symlink to a target outside the
  root).

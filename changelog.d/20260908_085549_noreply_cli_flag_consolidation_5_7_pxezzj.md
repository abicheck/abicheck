### Fixed

- **Action: release-topology config overlay no longer drops the project's
  own `.abicheck.yml`** — enabling `dso-only`/`include-private-dso`/
  `fail-on-removed-library` (or any of the cross-compilation inputs
  `add_compile_context_flags` covers) on the GitHub Action synthesized a
  minimal `--config` overlay and forwarded it directly, which — since an
  explicit `--config` fully replaces `_resolve_compare_config`'s own
  auto-discovery rather than augmenting it — silently dropped every other
  setting the repository's own auto-discovered `.abicheck.yml` carried
  (`severity:`, `suppress:`, `scope.on_incomplete`, `bundle:`, ...).
  `action/run.sh` now discovers the project's config itself and merges the
  synthesized keys into a copy of it before writing the file `--config`
  points at, with the Action's own inputs winning on a key conflict.
- **Runtime diagnostics no longer name the removed `--on-incomplete-scope`
  flag** — `policy/scope_completeness.py` and `report/comparison_scope.py`
  (plus `report/junit_scope.py` and `frontends/cli/release_dry_run.py`)
  now point users at `.abicheck.yml`'s `scope.on_incomplete` key, and
  `action/run.sh`'s own step-summary/warning/error text follows suit.


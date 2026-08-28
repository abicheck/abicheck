### Changed

- **ADR-061 continuation**: physically migrated the `policy` responsibility
  package's `legacy_paths` implementation modules -- `severity.py`,
  `exit_decision.py`, `contract_coverage_exit.py` -- from flat
  `abicheck/<name>.py` to `abicheck/policy/<name>.py`, a real subpackage
  (`abicheck/policy/__init__.py`/`AGENTS.md`, mirroring the existing
  `abicheck/model/`/`abicheck/compare/`/`abicheck/storage/` precedent).
  Every existing import path -- `from abicheck.<name> import X`,
  `from abicheck import <name>`, `import abicheck.<name>` -- keeps
  resolving identically: the old flat path is now a thin, static
  back-compat shim (`from .policy.<name> import X as X, ...` plus a
  matching `__all__`) re-exporting the moved module's public surface by
  value, so `abicheck.severity.compute_exit_code is
  abicheck.policy.severity.compute_exit_code` holds. A static re-export was
  used rather than `cli_buildsource.py`'s lazy `__getattr__` shim pattern,
  since nothing under `abicheck/policy/` needs to import back through the
  flat path -- no import-cycle risk to work around. Internal callers that
  are themselves already-migrated package code (`abicheck/workflows/gate.py`)
  were updated to import the new canonical `abicheck.policy.<name>` path
  directly; every other (still-flat) internal caller is left importing the
  old relative path unchanged, since it resolves through the shim exactly
  as before and updating ~50 call sites purely for style would add churn
  with no behavioral benefit. `scripts/classify_perf_paths.py`'s
  performance-sensitive-path list gained the new `abicheck/policy/
  severity.py` entry alongside its existing `abicheck/severity.py` one.

  `analysis_assurance.py` -- the fourth file `policy`'s `legacy_paths`
  listed -- was deliberately **not** moved. It already carries an
  `architecture/debt.yaml` `no_growth` entry (1539 lines, over the 800-line
  production cap) whose own recorded rationale is "cannot move safely
  without a behavior-preserving vertical slice," and
  `scripts/check_architecture.py`'s `debt-exemption` gate mechanically
  enforces exactly that: a debt-tracked file's path may not change within a
  PR whose base revision doesn't already carry a debt entry at the new
  path (it exists specifically to stop a debt entry from being silently
  relocated to dodge its own no-growth baseline). Moving it needs its own
  PR that both relocates the file and updates the debt-ledger entry as one
  deliberate act, not a side effect of this migration.

  Three of the moved modules' own dependencies -- `checker_policy.py`,
  `contract_gating.py`, `reclassify.py`, `contract_coverage_ledger.py` --
  are imported far too widely across every responsibility layer (including
  `frontends`, which may not import `compare`/`policy` at all) to safely
  receive a `legacy_paths` layer classification of their own; two of them
  (`contract_gating.py`, `reclassify.py`) already document themselves as
  deliberately-unclassified cross-layer leaves in their own module
  docstrings. Attempting to classify `checker_policy.py` as `compare` and
  `contract_coverage_ledger.py` as `policy` was tried first and reverted
  after `scripts/check_architecture.py` surfaced real `dependency-direction`
  violations in already-existing, unrelated code (`checker_types.py`
  importing `checker_policy` directly; two `cli_*.py` frontends importing
  `contract_coverage_ledger` directly) -- exactly the migration cascade
  the root `AGENTS.md`'s own "Known gaps" section already predicted for
  this area. All four stay unclassified; `checker_policy.py` and
  `contract_coverage_ledger.py` join `contract_gating.py`/`reclassify.py`
  in `architecture/modules.yaml`'s `public_root_surfaces` list (ADR-061
  D3's named escape hatch for behavior with no single clean layer owner),
  which exempts them from the `unclassified-import` check now that
  `severity.py`/`contract_coverage_exit.py` are real `migrated_source`
  files under `abicheck/policy/` and would otherwise be flagged for
  reaching them.

  Two mypy `attr-defined` re-export errors surfaced by the move (module
  `abicheck.policy.severity` not marking `ADDITION_KINDS`/`ChangeKind`/
  `HasKind`/`Verdict`/`PolicyError`/`is_evaluated`/
  `first_matching_reclassify_verdict` as explicitly re-exported, and
  similarly for `abicheck.policy.contract_coverage_exit`'s
  `coverage_exit_contribution`/`coverage_failures_for_context`) were fixed
  in the moved modules themselves with the same `X as X` redundant-alias
  pattern `severity.py` already used for its `reclassify.py` re-exports --
  a pre-existing minor inconsistency in that file's own import block, not
  something the move introduced, but only reachable by mypy once a second
  module (this migration's own shim) re-imported the names one hop further
  away. `tests/test_contract_coverage_exit.py`'s two direct references to
  the private `_coverage_message` helper were updated to import from the
  real `abicheck.policy.contract_coverage_exit` module instead of the flat
  shim, since a shim's `__all__` deliberately re-exports only the public
  surface. `architecture/modules.yaml` updates: the three moved paths
  dropped from `policy`'s `legacy_paths` (now auto-discovered under its
  real `path`); `abicheck/policy/AGENTS.md` added (required by
  `scripts/check_architecture.py`'s `scoped-instructions` check for any
  layer with a real, migrated source file).

  New `tests/test_policy_package_migration.py` pins both import paths (old
  flat and new `abicheck.policy.<name>`) resolving to the identical object
  for every re-exported public name across all three moved modules.

### Changed

- **ADR-061 continuation: classified `export_surface.py` into the `policy`
  responsibility layer** in `architecture/modules.yaml`
  (`policy.legacy_paths` now holds 13 entries on the merged tree).
  `export_surface.py`'s own cross-layer imports resolve only to `model`/
  `compare` (already-classified) — the exact set `policy`'s
  `may_import: [model, compare]` allows. It also imports `surface.py`
  directly, but that's a same-layer (`policy` -> `policy`) import — `surface.py`
  is itself already in `policy.legacy_paths` — which `may_import` doesn't
  need to name explicitly, so it doesn't change the dependency set this
  classification relies on. Nine further candidate modules
  (`policy_file.py`, `suppression.py`, `compatibility_evaluation_resolver.py`,
  `pack_application.py`, `contract_coverage_ledger.py`, `contract_gating.py`,
  `reclassify.py`, `checker_policy.py`, `pattern_verdicts.py`) were
  investigated and deliberately left unclassified for this PR — most because
  classifying them would surface pre-existing forbidden import edges
  (`frontends -> policy`, `model -> policy`, `compare -> policy`,
  `policy -> workflows`) the architecture gate would otherwise catch, and
  `pattern_verdicts.py` because its module mixes `compare`-shaped raw-change
  detection with `policy`-shaped classification. See
  `docs/contribute/adr/061-responsibility-package-architecture.md` and this
  PR's description for the full audit trail (which modules were already
  classified by sibling PRs, the exact import edges found, and the follow-up
  work needed to untangle each).

  Verification: `python scripts/check_architecture.py` -> 0 errors;
  `python scripts/check_ai_readiness.py` -> 0 errors; `mypy abicheck/` ->
  clean; full fast unit suite green.

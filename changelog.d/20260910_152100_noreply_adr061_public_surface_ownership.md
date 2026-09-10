### Fixed

- **Internal: redirect `checker_policy.py`/`qualified_name_segments.py`
  internal callers to their real ADR-061 gap B owners.** Two P1 Codex
  findings on this PR's own reconciliation work: of `checker_policy.py`'s
  ~103 internal callers, 93 needed no facade at all — 57 needed only
  `ChangeKind`/`HasKind`/`VALID_BASE_POLICIES` (already `model`-owned, not
  part of the classification/evidence-status split) and now import
  `model.change_catalog.kinds`/`model.change_catalog.registry` directly;
  36 more sit in a layer whose declared `may_import` includes `policy` and
  now import `policy.classification`/`policy.evidence_status` directly
  (splitting the import when a file needs both a model-owned and a
  policy-owned name). The 14 callers that remain on the facade each have a
  verified, structural reason: `checker_types.py` (`model`-classified,
  `model -> policy` forbidden), six `frontends`-classified CLI modules
  (`frontends -> policy` forbidden — confirmed empirically against
  `check_architecture.py`, not assumed), six `compare`-classified detector
  modules (`compare -> policy` forbidden), and one unclassified leaf,
  `surface_graph.py` — not layer-restricted at all, but caught instead by
  a dedicated, narrower test
  (`test_surface_graph_module_imports_nothing_from_policy`) asserting this
  specific module's own parsed imports never include `policy`, since
  `policy`'s own `PublicSurfaceQuery` calls *into* it and the reverse must
  never happen; redirecting it was tried and reverted once that test
  caught it. `sarif.py`, whose `report`-layer status *does* allow a direct
  `policy` import, was redirected. `qualified_name_segments.py`'s 4
  remaining internal callers (`dumper.py`, `dumper_hybrid.py`,
  `service_dump_native.py`, `serialization.py`) are redirected the same
  way, leaving zero internal callers on that facade. `architecture/
  dispositions.yaml`'s `checker_policy.py` entry and `architecture/
  debt.yaml`'s per-file `no_growth` baselines (plus one new entry,
  `bundle_detectors.py`, whose first-ever crossing of the 800-line
  production cap this split-import shape caused) are updated to describe
  the post-redirect state and account for the small (one-line-per-file)
  baseline growth this split-import shape costs where a facade import
  spanned both a model-owned and a policy-owned name.

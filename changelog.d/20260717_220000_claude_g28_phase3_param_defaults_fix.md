<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`--ast-frontend hybrid`** (G28 Phase 3 follow-up): `dumper_clang.py`
  gained `Param.default` support after the previous hybrid-merge fix
  landed, invalidating its "clang never populates defaults" assumption —
  the `param_defaults` detector was skipping a clang-only function on
  BOTH sides of a hybrid comparison (a legitimate same-producer pair, no
  different from a plain `--ast-frontend clang` run), silently missing a
  real default-argument removal/change. The gate now requires the SAME
  producer on both sides of a pair (not specifically CastXML), since the
  two backends' default *value* representations still aren't
  cross-comparable (CastXML keeps the real source expression; clang falls
  back to a structural fingerprint/placeholder for anything beyond a bare
  literal) even though both now capture presence/absence correctly.

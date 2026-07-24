<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`checker.compare()` now calls the ADR-050 D2 comparability gate**
  (G32 Phase A slice 2): a new `diagnostic_comparison: bool = False`
  parameter, and `check_contracts_comparable(old, new,
  diagnostic=diagnostic_comparison)` is called before any diff runs. By
  default a genuine `contract` mismatch raises `ProfileMismatchError`/
  `ScopeMismatchError` and no `DiffResult` is produced; passing
  `diagnostic_comparison=True` downgrades that to an ordinary diff whose new
  `DiffResult.assurance` field is stamped `"none"`. A new
  `DiffResult.contract_coverage` field is set to `"partial"` when exactly
  one side of a compare carries a `contract` at all (e.g. a fresh dump vs. a
  pre-ADR-050 stored baseline). **Still has no behavioral effect on a real
  `dump`/`compare` invocation** — `dumper.py` does not populate `contract`
  yet, so every snapshot produced today has `contract=None` on both sides,
  which the gate always treats leniently.
- Two correctness fixes to the `abicheck.comparability` fingerprint
  algorithm added in the previous PR, found in review: `scope_fingerprint`
  now recognizes the same header captured via `declared_headers` (a full
  L2 dump) and via `public_header_paths` (symbols-only provenance) as one
  shared scope identity rather than two separate ones, so an ordinary
  depth difference no longer spuriously reports a scope mismatch; and
  `profile_fingerprint`'s per-`-I`-slot ownership tokens now use each
  declared header's full normalized subpath instead of its bare basename,
  so two project-owned roots owning same-named headers in different
  directories (e.g. `include/foo.h` vs. `generated/foo.h`) no longer
  collapse to the same token and lose `-I`-order sensitivity.

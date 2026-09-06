### Added

- **ADR-065 S3 — package component inventories.** `abicheck/package.py` no
  longer answers "what does this package ship?" with just a directory. Every
  archive extractor now reports `ExtractResult.container_complete` (an
  archive extractor unpacks its container in full or raises; `DirExtractor`
  deliberately does not claim it), and `package_component_inventory` turns
  that plus the discovered members into a typed
  `PackageInventory` — declared components, an explicit `complete` flag, and
  a separate `unproduced` map, three answers that used to be one. A
  **package archive operand is therefore a completeness proof** for ADR-065
  D2 for the first time, beside S2's stored `inventory_complete` assertion;
  a plain directory operand still proves nothing, because a directory may
  have been populated partially. A declared component the extracted tree
  cannot reach (a dangling link, an unreadable file) is now
  `expected_not_produced` on the comparison-scope record — the acquisition
  state S2 reserved and left without a producer — never an absence the other
  side's proof may read as a removal.

- **ADR-065 S3 — support-promise findings (`--support-promise`).** A new
  contract-policy field on the directory/package `compare` fan-out, `off` by
  default. Under `--support-promise declared`, a component whose absence the
  other side's *proven-complete* inventory establishes is reported as a
  finding — the new `support_promise_component_retired` (BREAKING) and
  `support_promise_component_introduced` (compatible addition) change kinds —
  carrying the completeness receipt that justified it. Unlike
  `bundle_library_removed`, which fires only when a surviving sibling in the
  same release imports the missing library, a retired promise holds for a
  component with no intra-bundle consumer at all. An unmatched member under
  an unproven inventory never produces one, whatever the setting.

### Changed

- **ADR-065 S4 — the release fan-out's set difference is deleted.**
  `_match_release_keys` no longer computes `old_keys - new_keys` /
  `new_keys - old_keys` at all; pairing answers only *which* members have a
  counterpart, and what an unpaired member means is decided once, by the
  acquisition record. Its two remaining readers moved: the JSON
  `unmatched_old`/`unmatched_new` keys (unchanged in meaning — "members with
  no counterpart" — but now read off the record, and `[]` rather than a
  re-derived difference for a driver that has no record), and the fan-out's
  stderr notices, which are now written by
  `report.comparison_scope.release_scope_warnings` after the record exists.
  That is what lets one line distinguish a **proven** removal (`library
  removed: X`, naming the inventory that proved it) from an unmatched member,
  an `expected_not_produced` one, and an `out_of_scope` one — four states the
  set difference reported as one. Because the notices are derived from the
  record, they are emitted after the per-library fan-out rather than before
  it.

- **`--fail-on-removed-library` reaches exit `8` again for a package-archive
  pair.** ADR-065 S2 made exit `8` require a *proven* removal, which at the
  time only a stored `ProjectSnapshot`/bundle-facts capture could supply; S3's
  archive inventory now supplies one too, so `abicheck compare old.rpm
  new.rpm --fail-on-removed-library` exits `8` on a genuinely dropped
  component. A directory pair still exits `0` with an incomplete scope, which
  is the S2 behaviour and is unchanged. Migration note in
  `docs/reference/exit-codes.md`.

- The wheel filename-tag and `METADATA` fact readers moved from
  `abicheck/package.py` to `abicheck/extract/wheel_tags.py` (reading a
  declared build/packaging fact is `extract/`'s responsibility under
  ADR-061's routing table). `abicheck.package` re-exports every one of them,
  so `from abicheck.package import parse_manylinux_glibc_floor` and its
  siblings still resolve.

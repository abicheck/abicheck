<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **`--depth` now caps evidence for `compare`/`scan --against`, not only
  floors it.** `enforce_requested_depth` has long failed a run when the
  resolved evidence fell short of an explicit `--depth`, but never stripped
  richer evidence a pre-built JSON snapshot (or a directory/package
  operand) carried beyond what was requested — `compare old.json new.json
  --depth binary` could still emit real header-derived findings and publish
  `BREAKING`. Every `compare_snapshots()` call site that classifies a
  resolved pair (the native `compare` CLI, the typed-API/directory-package
  chokepoint, and `scan --against`'s baseline path) now projects each side
  down to the requested rung (`abicheck.policy.depth_projection.
  project_pair_to_depth`) first, so `--depth binary` behaves the same
  whether the input was freshly extracted or loaded from disk. A
  `binary`-depth projection now also correctly distinguishes a
  DWARF-informed snapshot (structural facts kept, matching a real
  DWARF-informed binary dump) from a purely header-derived one with no
  DWARF at all (fully stripped to bare symbol identity, matching a real
  headerless, debug-info-less dump). `dump`'s own `--depth` is unaffected
  (it stays floor-only, so a dumped artifact keeps whatever richer evidence
  extraction produced for a later, deeper comparison). An explicit
  out-of-band `--old/new-sources`/`--old/new-build-info` pack is now capped
  too — it previously bypassed the ceiling entirely — and the ceiling also
  now clears a stale `ExtractionContract` (which could otherwise raise a
  spurious scope-mismatch error), drops a non-exported (`HIDDEN`)
  declaration outright instead of misreporting it as a binary-visible
  removal, and filters `types`/`enums` by real per-record DWARF confirmation
  rather than a coarser whole-snapshot flag.

### Fixed

- A directory/package `compare` now models a multi-library release as **one
  public contract backed by several binary providers**, instead of comparing
  the complete product header surface against each member independently.
  Previously every declaration a *sibling* library provides was reported
  missing from this one: on a 28-library Intel MKL release that produced
  787,833 `public_not_exported` findings and a 1.6 GB report, with a
  ScaLAPACK declaration such as `BDLAAPP` demanded from `libmkl_rt` although
  another MKL library exports it. Binary evolution stays per matching
  library, with its own attribution; the export obligation is now reconciled
  once, at release level, against the union of the bundle's usable exports
  (the same default/unversioned-export projection the single-artifact check
  applies), so a declaration any member provides is satisfied and one nothing
  provides yields exactly one release-level finding carrying its declaring
  header — and no invented owning library. No header-to-library mapping,
  configuration, or symbol-family suppression is involved. On the two-library
  reproduction: 2 false findings -> 0, and a declaration removed from the
  whole bundle -> exactly 1 finding rather than one per member.

### Added

- Release reports carry a public-surface reconciliation section
  (`public_surface_reconciliation` in JSON, **Release public surface** in
  Markdown; release schema 1.8): per side, the public declarations with an
  export obligation, how many the bundle satisfies, the missing ones, the
  ones left *unresolved* because a member was unread, export totals and the
  documented/undocumented split, per-member undocumented-export counts, and
  the header-acquisition counts — one acquisition per side for an ordinary
  comparison, whatever the member count. A fact several members report
  identically (a changed public type being the usual case) is rendered once
  with every affected library named, and each member entry records how many
  of its findings were folded there (`product_level_findings`); per-library
  counts, verdicts and the exit code are unchanged by that fold.
- `BundleFacts.public_surface` (bundle-facts schema 4) stores the release's
  one acquired public contract and the acquisition identity it was acquired
  under, so a stored product baseline records *which* surface its members
  were reconciled against. A document without the block still declares its
  previous version and loads in every older reader; one carrying the block
  under a lower version is refused rather than silently read with the
  contract dropped.

### Changed

- The whole-product `public_not_exported` check no longer runs per member in
  a multi-member release — it is answered once at release level. A consumer
  reading per-member `public_not_exported` findings from a directory
  comparison finds them under
  `public_surface_reconciliation.missing_exports` instead, and far fewer of
  them. `exported_not_public` deliberately stays per member (the exporting
  member is its real attribution), and a one-member release keeps the
  per-member answer, so a one-member package and the scalar file-to-file
  path still agree. A member whose acquisition failed makes the
  reconciliation *incomplete* — obligations recorded as unresolved with the
  coverage gap named — rather than turning an absent symbol into a missing
  export.

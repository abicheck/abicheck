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

- The release-level public-surface acquisition now parses under the run's
  resolved `compile:` context (compiler, prefix, options/defines, sysroot,
  `nostdinc`, AST frontend and frontend context), not only keys on it. It
  hashed the context into its acquisition key while parsing without it, so
  a declaration behind `#ifdef FEATURE` with `compile.defines: [FEATURE]`
  configured never entered the product's contract: a two-library release
  saw 2 export obligations instead of 3 and exited 0, while the identical
  product as a single member reported `public_not_exported: api_c` and
  exited 2. The AST frontend is now derived from that same context by one
  resolver feeding both the key and the parse, closing the sibling case
  where two runs differing only in `compile.frontend` keyed identically.
  Found by Codex security review on PR #1328.

- The release contract reconciliation now sees a member's exports on **every**
  container format. `BundleSignatureEvidence` — the compact per-member evidence
  the release fan-out keeps instead of full snapshots — carried ELF metadata
  only, so on Windows and macOS the export index read *no exports at all* for
  every member. That is indistinguishable from a member exporting nothing, so
  coverage degraded to "incomplete" and every real missing-export finding was
  suppressed on both platforms while Linux was unaffected. The evidence now
  carries an already-projected, platform-agnostic export-name set (names only,
  through the same canonical `model.export_index` projection), appended so no
  positional caller rebinds. Caught by this change's own `integration-tests`
  matrix on `macos-latest`/`windows-latest`.

- The product model now applies to **every** multi-library comparison, not
  only a live directory/package `compare`: a stored `BundleFacts` baseline
  against a live release, two stored documents compared to each other, and
  a multi-library `compat check` descriptor each reconcile one product
  contract against the union of their members' exports, and each enters the
  same release-level check ownership for their member pass. Previously
  those three paths still asked every member for every sibling's
  declaration. A stored side uses the contract its capture recorded
  (falling back to one derived from its member snapshots for a pre-schema-4
  document); a side with no header evidence records no contract rather than
  borrowing the other side's, so a deliberately retired declaration is not
  read as a missing export. The stored-comparison document gains the same
  `public_surface_reconciliation` block (and Markdown section); the
  `compat` path, which has no release envelope, folds its release-level
  findings into the merged result.

- Review-round fixes to the above (CodeRabbit, PR #1328), each a real
  defect with its own regression test:
  - A release's resolved `PolicyFile` (a `--policy` document, plus pack and
    project overrides) now reaches the release-level verdict and severity
    exit. It reached only the member comparisons, so a document pinning a
    kind's verdict moved a member's finding and not the release-level
    finding that replaced it — moving a check's owner changed what it
    gates, the one thing that move may never do.
  - A bundle-facts **archive** stores the public surface as a
    content-addressed blob instead of inline in `manifest.json`. That
    manifest is capped at 64 MiB and the writer raises rather than
    producing an archive that exceeds it, while the surface grows with the
    whole product — so a large release could fail to write a baseline at
    all, at exactly the scale the block exists to serve.
  - `--output-dir`'s `summary.json` carries
    `public_surface_reconciliation` too. A CI consumer collecting the
    directory as its artifact reads that file rather than the primary
    report, so the product's contract was invisible to it.
  - The `ProjectSnapshot` import adapter refuses a document carrying a
    `public_surface` block it cannot represent even when that document
    omits `schema_version` entirely. The refusal keyed off the *declared*
    version, and an absent key defaulted to one below the block's, so such
    a document imported with its recorded contract silently discarded.
  - A capture stamps the schema version its own contents need (4 with a
    public surface, 3 for degraded members alone, 2 otherwise) rather than
    one "current" constant, which had left the returned in-memory
    `BundleFacts` declaring a version its contents contradict. Persisted
    documents were already correct.

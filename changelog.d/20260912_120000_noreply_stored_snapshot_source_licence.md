### Fixed

- **A stored snapshot's source-derived facts are no longer re-derived from the
  current filesystem.** `compare`'s lexical pattern and preprocessor pre-scans
  rebuilt their roots from each snapshot's recorded `source_header`/compile-unit
  paths and then read those paths off disk, so a stored baseline — typically
  published weeks earlier from a checkout that no longer exists on the runner —
  was re-characterised against whatever now sits at the same path. The contract
  is now explicit and enforced: **comparing stored snapshots uses stored facts;
  a path recorded in a snapshot is provenance, not a licence to re-read the
  current filesystem for historical facts.** A side is read only when it was
  extracted in this run, or when the caller supplies an explicitly
  provenance-verified source context (`compute_pattern_preprocessor_scan`'s new
  `old_source_licence`/`new_source_licence`); otherwise nothing is opened or
  even stat'd, and the report states that the historical evaluation was not
  possible instead of substituting today's tree. The licence is a runtime-only
  `AbiSnapshot` field the storage codec never writes, so a loaded snapshot
  cannot grant itself one.

- **Coverage sufficiency is computed from the expected input set, not from what
  a discovery walk happened to find.** `pattern_facts` skipped a non-existent
  root silently while completeness was `files_scanned > 0 and files_skipped == 0`,
  so a snapshot whose headers had all been relocated could still report full
  coverage off one surviving file. Every declared input is now accounted for by
  exactly one disposition — scanned, missing, unreadable, unsupported,
  deliberately excluded, or not licensed — and any gap leaves an absence claim
  unestablished; a root that did not exist can never read as fully covered.
  Sufficiency is also answered per check (the lexical scan, macro divergence and
  private-header leaks each answer to their own evidence) rather than by one
  global tally, and is reported per check and per side in the
  `pattern_preprocessor_scan` block's new `coverage` object.

- **`introduced`/`resolved` now require the side whose *absence* they assert to
  be established.** The evolution fold decided all four states from one global
  per-side completeness flag, which let a long-standing construct read as newly
  introduced whenever the OLD side's evidence was merely incomplete. Each
  identity is now decided from what is established *for that identity*: presence
  by observation (an incomplete scan cannot un-see a hit), absence only by that
  side's sufficiency. Undecidable identities read `not_evaluated` and are never
  dropped; `persistent`, which rests on two observations, is no longer withheld
  just because a side had a gap elsewhere.

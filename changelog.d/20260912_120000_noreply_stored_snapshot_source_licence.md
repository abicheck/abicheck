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
  cannot grant itself one. The licence is granted by the shared dump
  operation every front end funnels through (`service.run_dump`), so the typed
  Python API and the CLI behave identically on the same inputs.

- **Coverage sufficiency is computed from the expected input set, not from what
  a discovery walk happened to find.** `pattern_facts` skipped a non-existent
  root silently while completeness was `files_scanned > 0 and files_skipped == 0`,
  so a snapshot whose headers had all been relocated could still report full
  coverage off one surviving file. Every declared input is now accounted for by
  exactly one disposition — scanned, missing, unreadable, unsupported,
  deliberately excluded, or not licensed — and any gap leaves an absence claim
  unestablished; a root that did not exist can never read as fully covered. A
  directory that could not be enumerated counts too: `os.walk` reports
  traversal failures through a callback and ignores them without one, so an
  unreadable directory used to vanish from the account entirely rather than
  registering as a gap.
  Sufficiency is also answered per check (the lexical scan, macro divergence and
  private-header leaks each answer to their own evidence) rather than by one
  global tally, and is reported per check and per side in the
  `pattern_preprocessor_scan` block's new `coverage` object, computed from
  per-probe-family tallies (`family_attempted`/`family_succeeded`/
  `family_truncated`, preprocessor fact schema 3). The run-wide
  attempted/succeeded/truncated aggregates could not answer a per-check
  question: a compile-unit set truncated by
  `ABICHECK_PREPROCESSOR_SCAN_MAX_PROBES` marked the *header-leak* check
  insufficient even when every public header was probed, and a successful macro
  probe of a unit defining none of the curated ABI macros left `tus_scanned` at
  zero, so an ordinary build could never establish the absence of a macro
  divergence at all.

- **`introduced`/`resolved` now require the side whose *absence* they assert to
  be established.** The evolution fold decided all four states from one global
  per-side completeness flag, which let a long-standing construct read as newly
  introduced whenever the OLD side's evidence was merely incomplete. Each
  identity is now decided from what is established *for that identity*: presence
  by observation (an incomplete scan cannot un-see a hit), absence only by that
  side's sufficiency. Undecidable identities read `not_evaluated` and are never
  dropped; `persistent`, which rests on two observations, is no longer withheld
  just because a side had a gap elsewhere.

- **A new `AbiSnapshot` field must be deliberately classified as persisted or
  runtime-only.** `storage/legacy_sections.py`'s completeness gate caught
  `live_source_evidence` being neither; it is now registered as never appearing
  in a document, which is the invariant rather than an omission — persisting the
  source-read licence would let a stored snapshot grant itself permission to
  re-read today's filesystem.

- **The published JSON schemas describe the block, not just permit it.** Both
  copies of `audit_report.schema.json` (packaged and mirrored under
  `docs/reference/schemas/v1/`) now advertise `1.5` and describe
  `pattern_preprocessor_scan` — its `coverage` object, each pattern side's
  `sufficient`/`inputs` account, and the per-family probe tallies — instead of
  declaring it an opaque object. `compare_report.schema.json` gains the same
  described block, sharing one set of `$defs`, so a consumer of either report
  recognises the other. An opaque declaration validates every document, which
  is why a schema-shape test now pins the described keys rather than relying on
  positive validation alone.

- **The licence requires that the extraction actually *read* the recorded
  source paths, not merely that it ran in this session.** A headerless dump
  derives every declaration's `source_header` from DWARF's `DW_AT_decl_file` —
  a path on the *build* machine that the run never opened, and which on a
  downloaded binary either does not exist locally or belongs to something
  else. Granting the licence to any live extraction therefore reopened the
  original hole through the binary's debug info. It is now granted only for
  header-derived provenance, where the AST frontend genuinely opened the files
  it attributes declarations to. **User-visible:** a headerless
  `compare old.so new.so` now reports the pattern/preprocessor facts as not
  evaluated instead of characterising whatever occupies those paths; pass `-H`
  to get them.

- **A `--no-baseline` audit carries its candidate-side `coverage`.** The
  one-sided projection dropped it along with the evolution maps. The evolution
  maps genuinely cannot be stated without an OLD side; coverage can, and it is
  the only thing that distinguishes "the candidate has none of these
  constructs" from "we could not look".

- **A candidate the scanner cannot examine is a gap, not a filtered file.** The
  extensionless-header heuristic has to read a file to classify it, and treated
  a read failure as "binary" — so an unreadable extensionless header (an ACL, a
  transient I/O error) was dropped by the scannability filter without ever
  being recorded, and a sibling file scanning successfully let the set report
  full coverage over a header nobody read. Discovery now classifies tri-state:
  a real candidate, a file that was never evidence, or a candidate that could
  not be examined. Only the last is a gap, and only the middle is silent. A
  candidate whose `is_file()` or classification raises is likewise recorded as
  unreadable rather than aborting the advisory walk.

- **The verified-context override is reachable through `compare()`.** It began
  as a parameter on the workflow helper alone, which `checker.compare()` called
  without — so the documented case was unusable except by mutating an internal
  runtime flag. `compare(..., old_source_licence=, new_source_licence=)` now
  forwards it. Deliberately no CLI flag: "trust these paths" is a claim only a
  caller that verified them can make.

- A dangling symlink among the declared inputs is reported `unsupported` rather
  than `missing`. Both are gaps, so sufficiency is unchanged; the label is the
  point — `missing` sends a reader looking for a deleted file when the link is
  plainly still there.


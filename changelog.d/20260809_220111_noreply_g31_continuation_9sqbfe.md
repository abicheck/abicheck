<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **The direct-clang (`--ast-frontend clang`) L2 header backend now
  populates `TypeField.default` (the default member initializer)** —
  G31 Phase C's last remaining fact-completeness gap that backend can
  close (vptr *placement* still cannot; the member-initializer *value*
  now can). Previously castxml-only, which left
  `FIELD_DEFAULT_INITIALIZER_REMOVED`/`_CHANGED` silently dead on a
  `--ast-frontend clang` run.
- **`FIELD_DEFAULT_INITIALIZER_REMOVED`/`_CHANGED` now gate on a
  SAME-producer check** (mirroring `Param.default`'s own
  `PARAM_DEFAULT_VALUE_*` gate) instead of "castxml on both sides": the
  two backends' initializer VALUE representations are still not
  cross-comparable (castxml keeps the verbatim source expression, clang
  falls back to a literal/structural fingerprint), so a castxml-vs-clang
  pair is correctly declined while a clang-vs-clang (or castxml-vs-castxml)
  pair now compares for real.
- **Snapshot schema bumped to v20** to gate the clang-side field-default
  extraction above: a snapshot serialized on the `--ast-frontend clang`
  header path under an older schema version never actually extracted
  `TypeField.default`, so reloading it now correctly marks the fact
  unreliable (`AbiSnapshot.clang_field_initializer_facts_reliable`,
  mirroring `clang_deprecation_facts_reliable`'s v19 pattern) instead of
  treating a stale `None` as a trustworthy "no initializer" answer.
- **Qualified the hybrid merge's field `default` provenance key** by
  namespace, matching `deprecated`'s existing qualification: since a
  clang-only field's `default` provenance is now stamped too (previously
  only `deprecated` was), two distinct types sharing only a bare leaf name
  in different namespaces could otherwise collide in the shared provenance
  dict. A hybrid baseline persisted before this fix still reads correctly
  via the existing bare-key fallback.
- **Bumped the whole-snapshot disk cache version** (`snapshot_cache.py`,
  v7 → v8): an upgrading user's warm `--ast-frontend clang`/`hybrid` cache
  entry would otherwise keep replaying the pre-upgrade snapshot (missing
  the newly-extracted field-default facts, or stale bare-keyed
  `fact_provenance` for a hybrid entry) until the entry expired or was
  manually cleared.
- **Fixed a false `FIELD_DEFAULT_INITIALIZER_REMOVED` against a legacy
  pre-v20 clang snapshot** (Codex review, fresh evidence): the new
  same-producer gate's "producer unknown → permissive" fallback couldn't
  tell a POSITIVELY known-unreliable value (the legacy snapshot's
  unconditional `None`, real but wrong) apart from genuinely-never-recorded
  provenance, so comparing a fresh clang snapshot's real initializer
  against an unchanged, persisted pre-v20 clang baseline reported a
  spurious removal purely from the schema upgrade. The gate now declines
  the comparison outright whenever either side is positively known
  unreliable, while staying permissive for truly unset provenance.
- **Extended that same fix to cover a legacy pre-v20 hybrid snapshot**
  (Codex review, fresh evidence, second round): a hybrid merge's
  clang-only-appended record types never had `default` provenance stamped
  at all under the old merge code (only `deprecated` was), so an ABSENT
  provenance entry for one of those fields on a legacy hybrid snapshot is
  real-but-WRONG legacy data too, not genuinely unrecorded — the same
  reliability marker (`clang_field_initializer_facts_reliable`) now also
  covers the `"hybrid"` producer. A MATCHED field's own recorded
  provenance entry (always unconditionally stamped `"castxml"`, regardless
  of schema version) stays trusted either way — only an absence on a
  legacy hybrid snapshot is treated as unreliable.

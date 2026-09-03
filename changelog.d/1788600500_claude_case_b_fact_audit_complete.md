### Documentation

- **ADR-063 Phase 5B's fact-consumption audit is now complete for every
  model field carrying a `Fact[T]` sibling.** Audited the entire remaining
  case-(b) field inventory (`Function`/`RecordType`/`Variable`/`EnumType`
  fields whose bare resting value is already unambiguous, plus six
  snapshot-level fields with no detector consumer at all) and found zero
  fabrication risks — every pairwise finding-emitting detector already
  declines correctly on missing evidence. No code changes; recorded in
  `docs/contribute/plans/one-semantic-pipeline.md`.

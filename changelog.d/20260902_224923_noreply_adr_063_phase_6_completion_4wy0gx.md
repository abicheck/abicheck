### Added

- **`CanonicalEntity.template_arguments` is now populated for record
  occurrences** (ADR-063 Phase 6's sixth slice). A concrete class-template
  specialization's `RecordType.qualified_name`/`name` already embeds its
  full `Name<Arg1, Arg2>` compound spelling on every backend that surfaces
  one at all (confirmed with real castxml and DWARF output), so
  `extract/semantic_normalizer_template_args.py`'s new
  `split_template_arguments` decomposes it with a pure, backend-agnostic
  bracket/paren/angle-aware text split — no new identity work, no
  producer-specific branch. Each argument is stored verbatim (never run
  through type canonicalization, since a plain text split cannot tell a
  type argument from a non-type one apart from its own text alone); a
  non-template record gets a confirmed `Fact.present(())`. A closure-typed
  argument's raw `"(lambda at ...)"` marker is picked up and canonicalized
  to a stable ordinal by the pre-existing
  `qualified_name_segments.renumber_anonymous_closure_identities` pass
  (`template_arguments` was never excluded from its walk), matching the
  identical marker already renumbered in the record's own
  `canonical_spelling`/`EntityId` — verified end to end against a real
  compiled fixture instantiated with a real lambda's closure type.
  `dumper_clang.py`'s `parse_types()` never surfaces a concrete template
  specialization as its own record at all (only the uninstantiated
  pattern) — a confirmed, named gap, not a wrong fact: every clang-produced
  record correctly reports `Fact.present(())` (none of them are ever
  instantiations), but clang produces no comparable occurrence for a
  closure-parameterized template to agree with castxml's real one. A
  function template's own instantiation-argument list (only recoverable
  from the Itanium-mangled name, not the bare `Function.name`) remains a
  separate, unattempted gap.

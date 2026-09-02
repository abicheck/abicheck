### Added

- **ADR-063 Phase 5's fact/capability registry is fully populated:
  `Param.is_restrict` and `Variable.access` converted to `Fact[T]`**
  (schema v40). These were the last two entries of
  `KNOWN_UNCONVERTED_ELIGIBLE_FACTS`, which is now the empty set — every
  availability-ambiguous model field in the phase's scope carries a
  `Fact[...]` sibling and exactly one `FactDefinition`, and the
  `fact-registry-completeness` gate keeps it that way (a newly added
  eligible field fails outright rather than joining a baseline).
  `Variable.access` is the registry's one enum-valued fact, so its decoded
  value is rebuilt into a real `AccessLevel` member — the same
  non-JSON-native reconstruction `elf_binding_fact` already needed.
  `Param`'s encode/decode wiring moved from one hardcoded `is_va_list_fact`
  line to the per-owner tuple + `decode_param_facts()` shape every other
  owner uses.

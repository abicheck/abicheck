### Performance

- **A finished release member no longer pins its declarations.**
  `BundleSignatureEvidence` was described as compact but held
  `snapshot.function_map`/`variable_map` by reference, keeping every
  `Function`, `Variable`, `Param`, type spelling and `Fact` alive for the
  whole release. Its consumer asks only two yes/no questions per symbol, so
  those are now resolved up front — by the same authoritative predicates,
  so a compact member and a full-snapshot member cannot disagree — and only
  the answers are kept. Measured on a real oneDAL `libonedal_core.so.1`
  (89,997 symbols): **146.6 MiB → 24.8 MiB, 5.9x**, freeing 121.8 MiB per
  member side. JUnit and `--bundle-facts-out` still receive full snapshots.
- **Request-resident AST retention is bounded and releasable.**
  `AstAcquisitionScope` kept every parsed AST root alive for the whole
  request, because some of its acquisition keys are derived from `id(root)`
  and a freed object's address can be reused. Retention is now *grouped*:
  an id-keyed entry is recorded against the object whose id it names, and a
  group is released whole — object and derived entries together — so no key
  mentioning a freed id can survive its object. Beyond
  `MAX_RETAINED_CONTEXT_GROUPS` the least-recently-used group is released,
  which costs a re-parse and never a wrong answer. `retain()` is also now
  idempotent per object (it is called once per member, and single-flight
  means members share roots), and `group_stats()` reports retained groups,
  entries and releases so a memory trace can attribute residency to shared
  raw ASTs rather than to per-member evidence.

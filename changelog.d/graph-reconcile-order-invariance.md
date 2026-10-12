### Fixed

- **Identity-reconciliation findings no longer change between reruns** — a
  declaration redeclared in several headers had its declaring file chosen by
  `SOURCE_DECLARES` edge order, so the same header set on both sides read as
  `declaration_identity_unchanged` on one run and the RISK-tier
  `declaration_moved` on another (libonedal_core: 3 vs 21 RISK findings).
  The graph reconciler now considers every declaring file (a shared one is
  never a move), reconciles its inputs in canonical id order, and
  `decl_declaring_files` picks the smallest label instead of the first edge.

### Fixed

- **A dropped ISA family of public functions is now BREAKING** — the
  `cpu_dispatch_isa_dropped` roll-up (three or more `*_avx2`-style exports
  removed while siblings under another ISA survive) graded every such removal
  `COMPATIBLE_WITH_RISK`, even when the removed functions were declared in the
  public headers and a direct caller no longer loads. The roll-up still names
  the family once, but now carries `effective_verdict: BREAKING`
  (`modulation_rule: cpu_dispatch_isa.public_member`) when any removed member
  is header-declared; it stays a risk only when every member is export-only.
  Catalog case83 (whose specializations are header-declared) now expects
  `BREAKING`, exit 4. CI gates that passed such a release will now fail.

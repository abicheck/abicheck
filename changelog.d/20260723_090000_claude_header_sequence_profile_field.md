<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`profile_fingerprint` never captured declared-header order** (Codex
  review, PR #624): the aggregate driver TU dumper.py generates includes
  declared headers sequentially in the caller's given order, so a
  macro/pragma side effect from one header can change how a later header in
  the sequence parses — `-H a.h -H b.h` and `-H b.h -H a.h` can genuinely
  produce different ASTs even though the same header set is declared either
  way. Neither fingerprint tracked this: `scope_fingerprint`'s "headers"
  field is (deliberately) a sorted set, and `profile_fingerprint` had no
  field for header order at all, so a reordering that could change the
  extracted AST went completely uncaught by the comparability gate. Added a
  new `header_sequence` profile field — an order-preserving, deduplicated
  list of declared headers' identities, populated whenever an L2 frontend
  ran. `scope_fingerprint` deliberately stays order-independent (the
  declared *surface* — which headers are public — doesn't depend on dump
  order); only `profile_fingerprint` (the extraction *context*) now tracks
  it, mirroring how `-I` search-path order is already tracked via
  `include_sequence` rather than folded into scope.

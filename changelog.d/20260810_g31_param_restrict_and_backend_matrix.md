<!--
A new scriv changelog fragment.
-->

### Fixed

- **Cross-backend false `param_restrict_changed` (G31 Phase C).** `Param.is_restrict`
  was populated by the CastXML L2 backend alone — `dumper_clang.py` left every
  parameter at the model default `False` — while `_diff_param_restrict` compared the
  two bools directly, with no producer gate. Comparing a CastXML-parsed side against
  a clang-parsed side of *unchanged* headers therefore reported
  `param_restrict_changed` for every `restrict`-qualified parameter. The direct-clang
  backend now extracts the qualifier itself (`_clang_param_is_restrict`, verified
  against real `clang -ast-dump=json` output for the C `restrict`, C++
  `__restrict`/`__restrict__`, typedef-indirection, and pointee-vs-parameter
  spellings), so both backends answer the same question the same way.

  Two gates come with it. The detector is now **header-tier only** — DWARF, PDB and
  the symbol-table paths never populate this fact, so their `False` meant "not
  collected", not "not qualified" — and snapshot schema **v22** adds
  `clang_restrict_facts_reliable`, which declines the comparison against a persisted
  pre-v22 clang/hybrid baseline whose blanket `False` is real-but-wrong data. The
  whole-snapshot disk cache version is bumped to `10` so a warm cache re-extracts
  rather than replaying a snapshot that predates the fix.

### Documentation

- **New: [Header-Backend Capabilities](https://abicheck.readthedocs.io/en/latest/reference/header-backend-capabilities/)
  (G31 Phase D)** — the per-fact contract for the L2 header layer's two parsers and
  their merge: which of the 95 model fields CastXML, direct-clang, and
  `--ast-frontend hybrid` each populate, why CastXML cannot supply the graph's
  call/reference edges, and which of five clang-extension routes fits a given fact.
  The tables are generated from `scripts/backend_capabilities.py`, and
  `tests/test_backend_capability_matrix.py` re-derives every published claim from the
  two parsers' own source, so the page cannot drift from the code.

  The matrix documents several previously-unrecorded gaps it surfaced, among them:
  `EnumType.underlying_type` is populated only by clang (a CastXML or hybrid enum
  keeps the default `int`); a hybrid merge is castxml-based, so clang-only facts it
  does not explicitly backfill are dropped for any declaration both backends saw; an
  opaque handle type is *absent* from a clang snapshot rather than opaque; and
  `Variable.value`, `Variable.access` and `Param.is_va_list` have no producer on any
  layer today.

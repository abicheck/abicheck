# G32 Phase 0 fixtures

Regression fixtures for [ADR-050](../../../docs/development/adr/050-comparability-contract-and-multi-tu-manifest.md)
/ [G32](../../../docs/development/plans/g32-comparability-contract-and-multi-tu-manifest.md)
Phase 0. These are raw inputs (headers, and one real AST capture) for later
phases to load — not generated `.abi.json` snapshots, and (per Phase 0's own
"Out of scope") no production code reads any of this yet. `tests/test_g32_fixtures.py`
only asserts the fixtures themselves are present, non-empty, and structurally
sane.

## Fixture 1 — plain-clang AST capture vs. DPC++ multi-document capture

`plain_clang/header.h` is a small header; `plain_clang/ast_dump.json` is a
**real** `clang -x c++ -std=c++17 -fsyntax-only -Xclang -ast-dump=json
header.h` capture (Ubuntu clang 18.1.3), captured from inside this directory
so the embedded `"file"` path stays a stable, repo-relative `header.h`
rather than a throwaway absolute path. It parses as one JSON document (a
single `TranslationUnitDecl`).

**Known gap, not fabricated:** Phase 0 also calls for "a real captured
DPC++/`clang -ast-dump=json` multi-document output fixture ... from an
actual `icpx`/DPC++ invocation, not synthesized" — the concatenated
host+device document stream Phase D's stream parser is meant to be designed
against. No `icpx`/`dpcpp`/Intel oneAPI toolchain is available in this
environment (only stock `clang`/`clang++`/`gcc`/`g++`), and Phase 0's own
point is "don't build a stream parser against a guessed format; capture the
real thing first" — synthesizing a fake multi-document stream here would
violate exactly the principle this phase exists to uphold, so it is
deliberately left uncaptured rather than faked. **This fixture must be
captured on a host with a real DPC++ toolchain before Phase D's stream
parser design starts**; `plain_clang/ast_dump.json` above stands in only as
the single-document contrast case in the meantime.

## Fixture 2 — ODR-safe merge pair and ODR-conflict pair

- `odr_safe/tu_a.h` forward-declares `struct Point`; `odr_safe/tu_b.h` gives
  it a full definition. A correct multi-TU merge (Phase C) combines these
  into one complete `Point`, not a conflict.
- `odr_conflict/tu_a.h` declares `int compute(int)`; `odr_conflict/tu_b.h`
  declares `double compute(int)` — the same name, genuinely incompatible
  signatures across two TUs, which a correct merge must reject.

## Fixture 3 — external STL noise

`stl_noise/public.h` declares `int sum_all(std::vector<int> values)` — a
genuinely public, reportable declaration whose signature also pulls in a
`std::vector<int>` instantiation that is supporting, not itself reportable
(ADR-024's public/private/external `ScopeOrigin` boundary — not redefined
here, only exercised at the merge layer once Phase B/C exist).

## Fixture 4 — scope drift

`scope_drift/old/` and `scope_drift/new/` declare the identical `a.h`/`b.h`
pair (byte-for-byte); `new/` additionally declares `c.h`, one extra TU with
no counterpart on the old side — a manifest/CLI-flag drift between two
extraction runs, not a real API change. Once Phase A's comparability gate
exists, comparing `old/` against `new/` must hard-fail `not_comparable` by
default, and fall back to a tentative, `assurance: "none"`-stamped diff only
under the explicit `--diagnostic-comparison` opt-in.

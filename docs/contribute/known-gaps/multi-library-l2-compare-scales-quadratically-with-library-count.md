---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Multi-library L2 compare scales quadratically with library count

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The memoization landed: the libraries budget is 1.7 in scripts/check_l2_scaling_perf.py:103 and header_scan_memo/include memo exist. Still open: every member walks the union header set and runs the per-member field parse (step 2 of entry 73).


Found by `scripts/check_l2_scaling_perf.py` (2026-09). A directory `compare`
of N generated libraries (2 public headers each, shared dependency context,
`--depth headers`, cold cache, 4 CPUs) costs:

| libraries | 1 | 3 | 6 | 7 | 10 | 16 |
|---|---:|---:|---:|---:|---:|---:|
| wall (s) | 1.3 | 2.2 | 5.0 | 6.9 | 11.5 | 32.2 |

Above the ~1.2 s fixed floor, the added cost grows with a marginal exponent of
~1.6 over 1–10 libraries and ~1.9 between 7 and 16. The directory compare is
therefore close to quadratic, not linear, in the member count. Peak RSS stays
flat (~230 → 365 MB), so this is time, not memory.

**Partly fixed (2026-09).** py-spy (the first cProfile reading misattributed
worker-thread time to `bundle_symbol_status`) showed the per-member time
going to work over the union header set. The two largest pieces are now
memoized:

- extraction-contract fingerprinting resolved paths on every dependency ×
  header/include-root test, about 35% at 16 libraries. It now resolves each
  path once per computation (`comparability_fields`).
- the C++20 dialect scan ran several times per dump. It is now
  content-validated and memoized in-process (`extract/header_scan_memo.py`).

Result: 10 libraries 11.5 s → 8.6 s and 16 libraries 32.2 s → 19.1 s, with
the marginal exponent over 1–10 falling from 1.64 to 1.39. The gate budget is
now 1.7.

**What remains** is many small per-member walks of the union set: header
expansion, inferred include roots, dependency-scope roots, one `clang -M`
include probe per header, and the include-graph gate's deliberate per-probe
memory re-read. Each is linear per member, so the release is still
super-linear. This is the performance face of the "`-H`/`--header` set is
applied to every member" entry below. Its steps 2/3 remove all of these at
once: give each member only its own headers, or compute the release-scoped
part once. Lower the budget toward ~1.1 when that lands. The oneDAL receipts
(`performance.md`, "oneDAL solo L2 compare") involve a handful of libraries
with 125 header roots, which is where this term shows up at real scale.

**Measured on MKL (2026-09-27): the header *parse* is not per member.** A
reported "56 header parses for 28 members" counted the `header AST parse`
progress lines, which one member dump emits whether it runs the frontend or
waits on another worker's run. Instrumenting a 3-member MKL release
(2024.2.2 → 2025.2.0) showed the frontend runs once per side per AST key,
shared through the request's acquisition table (`dumper_cache.
run_ast_acquisition`). The one real duplicate was the release public-surface
parse keying apart from the member dumps on a relative `-I` spelling (fixed:
`resolve_header_ast_result` absolutizes for every caller), which took castxml
runs from 4 to 2. What *is* per member, measured: the export-bound field
parse over the shared DOM (`extract/header_ast_fields.
_parse_header_ast_legacy`, dominated by `parse_functions`: ~2.5 s alone,
~7 s under 4-way contention, per member-side). It is per member on purpose --
visibility, constructor/destructor fallbacks and the surface facts are
decided from that member's export table while the declaration is built
(that module's own docstring) -- so sharing it means splitting each
`Function` into an export-neutral parse plus a per-member binding step. That
split is the remaining lever for a large release, and the RSS it would save
is the per-member declaration lists, not DOMs.

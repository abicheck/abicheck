# ADR-079: Declared Header Order in the Aggregate Driver Translation Unit

**Date:** 2026-10-12
**Status:** Proposed — not implemented. Decides how the header AST is built when
the declared order of `-H` headers matters. Blocks the behaviour change that the
known gap "Declared-header order is still load-bearing because the driver TU is
sequential" asks for. Governs extraction output, so it must be accepted before
any code change to the aggregate TU.

## Context

The castxml and clang backends parse every declared public header in one
aggregate translation unit, `#include`-ing them in the caller's declared order
(`extract/headers/ast_config.py::clang_aggregate_text`,
`extract/castxml_header_compat.py::castxml_aggregate_text`). A header's macros,
pragmas and typedefs therefore affect how every later header parses.
`comparability_fields._declared_header_sequence` records that order in
`profile_fingerprint`, and the comparison gate refuses a reorder of existing
headers. Adding or removing headers is handled separately (scope-confirmed
growth is waived, see `comparability_sequences._header_sequence_is_scope_confirmed_growth`).

The gap asks that reordering existing headers stop refusing. Measured on
2026-10-12 with two headers, `z_base.h` (defines `Base`) and `a_user.h`
(declares functions over `Base`, no include), built into one shared library:

| Declared order | castxml | clang |
|---|---|---|
| `z_base.h, a_user.h` | both functions extracted | both functions extracted |
| `a_user.h, z_base.h` | L2 header parse fails for `a_user.h`; header excluded with reduced evidence; **zero** declarations | `SnapshotError`: unknown type name `Base` |
| `a_user.h` alone | — | `SnapshotError` (the same unknown type) |

Two facts follow from the measurements:

1. **Sorting the header set is not output-neutral.** A canonical sorted order
   (`a_user.h` < `z_base.h`) is the second row, so sorting turns a valid declared
   order into a failed or empty extraction for any header that depends on an
   earlier one. Canonicalising the order is rejected.
2. **Removing the refusal alone is unsound.** Dumping the same unchanged library
   in both orders and comparing them with the gate bypassed reports
   `BREAKING`, with `func_removed make_base` and `func_removed base_x`. The
   refusal is what keeps an order-dependent extraction from becoming a false
   break.

## Options

**A. Keep declared order and the refusal (status quo).** Sound. Every reorder of
an existing header still refuses, and the user must re-dump under the old order
to compare.

**B. Canonical (sorted) aggregate order.** Rejected: see fact 1. It converts a
valid declared order into a failed or empty extraction.

**C. One independently scoped TU per declared header.** Removes declared order
from the extraction for every header that is self-contained. The cost is that
a header which depends on another declared header (as `a_user.h` does) no longer
parses at all. Under clang that is a loud `SnapshotError`; under castxml it
falls back to reduced evidence. The per-header TU also multiplies parse cost by
the header count, which the known-gap write-up already records as the reason it
was not attempted with the disposition fixes. Changes extraction output for
every project with a dependent header set.

**D. Dependency-ordered aggregate.** Order the aggregate topologically from the
header `#include` graph, falling back to declared order for cycles and
undeclared dependencies. Keeps one TU and the current cost. Order-dependence
remains wherever the graph is incomplete (an include that is reached through a
macro, or a dependency outside the declared set), so the refusal cannot be
removed on this option alone.

## Decision needed

Accept one of C or D, or keep A. Recommendation: **D first**, measured on a real
multi-header project, because it keeps extraction output identical for every
header set whose declared order already matches its dependency order. Adopt C
only if D's residual cases are shown to be common. Whichever is chosen, the
refusal may be narrowed only for the headers the chosen option proves
order-independent. The gate's `header_sequence` field must keep refusing every
other reorder.

## Consequences

- No default, exit code, schema or public interface changes under this ADR
  until one option is accepted and implemented.
- Option C or D changes extraction output for some header sets. That needs a
  changelog fragment, and `tests/test_driver_tu_declared_order.py` pins the
  current behaviour and must be updated deliberately with the change.
- The known-gap entry stays OPEN until an option is implemented.

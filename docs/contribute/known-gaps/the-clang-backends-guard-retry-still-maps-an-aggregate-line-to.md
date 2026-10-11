---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# The clang backend's guard retry still maps an aggregate line to a header (2026-10-03)

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** abicheck/extract/headers/clang/error_header_retry.py:69-79 still maps the aggregate line index to the header. The entry notes it is latent: not broken unless a preamble is added. The writers are now in extract/headers/clang/backend.py:304 and clang_layout_tool.py:203.


`extract/unparseable_header_fallback.py` attributed a castxml diagnostic to
header `N-1` from the aggregate frame's line `N`. When the castxml aggregate
gained a preamble include on its first line, that rule dropped the healthy
neighbour of a rejected header and the directory dump failed. It now names
the input by the file the aggregate frame includes, which does not depend on
the aggregate's layout (bug class `extraction.aggregate_layout_inverted_by_line`).

The clang backend's direct-inclusion-guard retry
(`extract/headers/clang/error_header_retry.py`, `_headers_failing_in_aggregate`)
still uses the line rule. It is correct today: both of its writers
(`dumper.py`'s `_write_agg`, `clang_layout_tool.py`'s `_write_agg`) put one
include per line with nothing before them. A preamble added there would
reintroduce the defect; attributing by the included file, as the castxml
fallback now does, closes it. Not changed here because nothing is broken and
the function's test suite encodes the line layout in every case.

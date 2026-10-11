---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# A set of libraries pays full cost per library, and a shared header context does not reduce it

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): NEGATIVE_RESULT.** Recorded measurement: a shared dependency header does not reduce the extraction count (20 vs 20), and the entry explicitly declines to claim cross-library sharing should be built. Keep it as a measured result.


Five libraries compared as five per-library `compare` invocations (the only
supported shape — there is no declarative L2 bundle comparison), cost ~1.15–1.50 s
each and **4 header extractions each**, for a set total of ~6.3 s. That figure is
the same whether the five libraries share one dependency header or each have
their own:

| arm | resolved dependency headers | extractions (5 libraries) |
|---|---:|---:|
| shared context | 1 | 20 |
| distinct contexts | 5 | 20 |

The mechanism, which is the actually useful part: the header-frontend invocation
count is driven by the **top-level** headers, not by their dependencies, so
sharing a dependency header cannot reduce it. Each library still needs its own
parse of its own public header, and that parse pulls the shared dependency in
regardless of whether another library already parsed it.

**This claim was previously unsupported and is worth flagging as such.** The
first version of the fixture gave every library its own byte-identical copy of
`detail/core.h` under its own `libN/include/` path — and the AST cache keys on
each header's *resolved path*, so the "shared" arm shared nothing. The two arms
were both distinct-path workloads, and comparing them could only ever have
produced "no difference". The fixture now resolves the shared arm through one
physical file at a common include root, `header_contexts` is counted from the
resolved paths actually built rather than from the flag that requested them, and
the numbers above are from that corrected fixture. The conclusion happens to be
the same; the evidence for it did not exist before.

Recorded as a measurement of current behaviour, deliberately not as a claim that
cross-library sharing *should* be implemented: whether a cross-library
header-AST cache is worth its invalidation complexity is a real design question,
and `scripts/l2_real_profiles.py`'s oneDAL profile (five header-bearing
libraries across two compile contexts) is the realistic case to judge it
against.

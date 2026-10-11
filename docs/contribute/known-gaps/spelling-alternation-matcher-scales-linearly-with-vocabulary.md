---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Spelling alternation matcher scales linearly with vocabulary (nested same-offset bug fixed)

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The under-report half is fixed and pinned by tests/test_spelling_nested_same_offset.py. The entry itself says the alternation-scaling half (linear in vocabulary size) remains open.


**Status: fixed** in its own change, as this entry required (the
under-report half only -- the scaling half below remains open). Present at
`950efbc64` through `e9d820797`. `finditer_allow_nested` now probes every
boundary-valid offset and, at each one, enumerates candidates longest first
by re-matching with a lowered `endpos`, re-checking the right boundary
against the real text so the `endpos`-as-end-of-string trap described below
cannot accept `Foo` inside `Foobar`. It still uses the compiled alternation,
so it needs no second index and no change to any caller.
`tests/test_spelling_nested_same_offset.py` pins the table below and checks
the new matcher against an independent brute-force oracle (every substring,
both boundaries judged on the real text), plus the strict-superset property
against the previous implementation. The history below is kept for why the
fix is shaped this way.

`compare/spelling_pattern.py`'s `finditer_allow_nested` finds nested matches
by re-searching the window `(m.start() + 1, m.end())` after each match. That
window excludes `m.start()` by construction, so a **shorter registered
spelling beginning at the same offset as a longer match is never reported**.
The alternation is ordered longest-first, so the longer one always wins the
position and the shorter one has no second chance.

Reproduced on entirely realistic spellings (not randomized inputs) — in each
case the second vocabulary entry is the only match returned, and the first is
a boundary-valid occurrence that is lost:

| vocabulary | text | reported | lost |
|---|---|---|---|
| `Foo`, `Foo<int>` | `Foo<int>` | `Foo<int>` | `Foo` |
| `dal::Table`, `dal::Table<float>` | `const dal::Table<float>& x` | `dal::Table<float>` | `dal::Table` |
| `std::vector`, `std::vector<int>` | `std::vector<int>` | `std::vector<int>` | `std::vector` |
| `Node`, `Node*` | `Node* next` | `Node*` | `Node` |
| `A`, `A&&` | `A&& r` | `A&&` | `A` |

This is reachable in production rather than theoretical: `type_reachability`
registers record spellings and typedef targets into one vocabulary, so a class
template and an instantiation of it routinely co-occur there. The consequence
is a lost reachability edge, which can mean a lost finding.

**The obvious in-place repair is unsound.** Re-searching a *narrowed* window
to find the shorter alternative makes `re`'s `endpos` look like
end-of-string to the right-boundary lookahead, so `Foo` would be accepted
inside `Foobar` — trading an under-report for an over-report.

A correct fix scans each boundary-valid start position and tests the
candidates registered there. A prototype of exactly that (bucket spellings by
a fixed-length prefix; scan positions whose left neighbour is not a boundary
character) was differentially tested against `finditer_allow_nested` over
2,400 randomized vocabulary/text pairs: **70 divergences, every one a strict
superset** — 0 subsets, 0 incomparable. It never missed anything the current
matcher finds.

**Why it is not landed here.** Adding those occurrences adds reachability
edges, which can change findings and the release obligation counts. It
therefore needs its own isolated change and its own transparent rebaseline.
Bundling it into a performance patch would make that patch silently alter
results and would invalidate the semantic-equivalence check the performance
claim rests on — see `AGENTS.md`'s "Validate the user-facing result" and the
instruction to isolate a correctness change rather than ship it as an
optimization.

The same prototype is also the standing answer to the *scaling* half of this
area, measured on this host (microseconds per lookup, 34-character subject):

| vocabulary | 1,000 | 5,000 | 20,000 | 60,000 |
|---|---|---|---|---|
| alternation, diverse spellings | 5.7 | 25.0 | 204.3 | 917.6 |
| indexed scan, same inputs | 1.7 | 1.8 | 1.8 | 1.7 |

Build cost at 60,000 spellings: 1.44 s for the alternation (1.74M pattern
characters) against 0.024 s for the index. The alternation is flat only when
the vocabulary factors to a shared literal prefix or the subject fails on its
first character; for the diverse-namespace shape a real C++ vocabulary has,
a miss is linear in the vocabulary. `compile_spelling_pattern`'s docstring
claimed the scan was "independent of candidate count" and has been corrected.

Full measurements, the environment they were taken in, and an explicit
statement of what they do *not* establish are in
[`measurements/spelling-cache-admission.md`](../measurements/spelling-cache-admission.md).
Real oneDAL acceptance is no longer pending: it was measured on six matched
members assembled from published wheels — −19.8% wall (under the ≥20% target)
with bit-identical findings, and **no memory improvement**.

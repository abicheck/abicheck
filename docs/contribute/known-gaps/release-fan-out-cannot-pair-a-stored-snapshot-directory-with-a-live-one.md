---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Release fan-out cannot pair a stored snapshot directory with a live one

Index: [Known gaps — closed and moot](../archive/known-gaps-closed.md)

**Status (2026-11-01): CLOSED.** Members the file name cannot pair are paired by the library they record (`workflows/release_member_pairing`), without moving any pair the file name makes. See the archive entry for the rule, the rejected alternative, the tests and the stored-package NEW route fixed alongside it.

A directory (release) compare pairs members by their library key, which is the
file name: `workflows/release_inputs.py` intersects the two sides' key sets. A
directory of stored `abicheck dump` snapshots (`libfoo.json`) against a directory
of live binaries (`libfoo.so`) therefore pairs nothing. The run completes zero
comparisons, both members are reported unmatched, and the process exits 1 with
`no_comparison_completed`. The same bytes compared as scalar operands (`compare
libfoo.json libfoo.so -H ...`) compare normally, and so do the typed API and a
live directory against a live directory.

The mismatch is found by family F2's live route matrix
(`tests/test_family_f2_live_route_matrix.py`, `KNOWN_ROUTE_DIVERGENCES`), and is
pinned by the strict-xfail cells `release@mixed_old` and `release@mixed_new`. The
run is not a silent pass: the exit code and the `no_comparison_completed` reason
say that nothing was compared.

`hygiene` — A stored/live directory pair is a shape the release engine cannot
compare at all. The gap is the missing member identity, not a false clean.

Why it is recorded rather than fixed: pairing a stored snapshot with the live
binary it was dumped from needs a member-identity rule (library name, SONAME, or
a declared inventory). Changing the key changes which members pair in every
existing directory compare, so it is a design decision on the release engine,
not a local fix.

What would close it: key a member by its library identity rather than its file
name, so `libfoo.json` and `libfoo.so` of one library are one member. Then the
two strict-xfail cells XPASS, and their entries in `KNOWN_ROUTE_DIVERGENCES` are
removed. Until then, compare such a pair as scalar operands.

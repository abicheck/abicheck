# ADR-071: Declared and Exported Are Independent Facts

**Date:** 2026-09-12
**Status:** Accepted — implemented. Adds a pair of `Fact[bool]` fields to
`Function`/`Variable` (snapshot schema **v46**) and two `ChangeKind`s, and
changes what `diff_namespaces` reports for one input class. Uses
[063](063-one-semantic-pipeline.md)'s `Fact[T]` vehicle and its
availability-before-value rule; completes the fourth defect from the same
report [069](069-name-shape-is-not-contract-membership.md) records the
other three of. Owners: `abicheck/model/declaration_surface.py`,
`abicheck/model/declarations.py`,
`abicheck/extract/declaration_surface_stamp.py`,
`abicheck/compare/source_surface_removal.py`,
`abicheck/compare/export_axis_findings.py`,
`abicheck/diff_namespaces.py`, `abicheck/diff_symbols.py`.

## Context

`Visibility` has three members and its own comments show that they answer
two different questions at once:

```python
PUBLIC = "public"      # default visibility / exported
HIDDEN = "hidden"      # __attribute__((visibility("hidden")))
ELF_ONLY = "elf_only"  # present in ELF symbol table, not in headers
```

`PUBLIC` means "declared in a parsed header **and** dynamically exported".
There is no member at all for the fourth, entirely ordinary combination —
**declared and not exported**: an inline member, a function the optimizer
fully inlined away, one a `-fvisibility=hidden` or version-script change
stopped exporting.

Thirty-nine guard sites then wrote `visibility == Visibility.PUBLIC` and
meant, variously, "is this in the public API" or "does the binary export
this". Each got the question it meant right and the other one by
accident, and no test of either question alone could see it: the signal
is correct under one reading, every time.

The observable failure came from two real artifacts of one library with
**byte-identical headers**, differing only in export emission. A public
inline method still declared in both — `svs::runtime::v0::
DynamicVamanaIndex::add(size_t, const float*)`, against which both GCC 14
and Clang 17 compile an explicitly-qualified call from either header set —
dropped out of `diff_namespaces`' source-surface index on the new side and
was reported as a *source* removal. Nothing had been removed from
anywhere.

## Decision

**D1. The two facts are separate fields, and absent evidence is a third
answer.** `Function`/`Variable` carry `declared_fact` and `exported_fact`,
both `Fact[bool]`. Neither has a legacy sibling to bridge from, so an
omitted field resolves to `Fact.not_collected()`. That resting state is
the decision, not an implementation detail: a headerless dump must report
declaration presence as *unknown*, and a snapshot with no observed export
table must report export status as *unknown*. Reading either as a
confirmed `False` would let a missing producer fabricate findings for
every symbol in a library — the same fail-closed rule `export_surface.py`
already applies to an uncaptured export table.

**D2. `Visibility` stays.** It is a correct, useful summary and it is
persisted in every existing snapshot. This ADR adds facts beside it; it
does not rewrite the enum, and no consumer is required to migrate.

**D3. One module owns how the pair is read.**
`model/declaration_surface.py` exposes the two ternary answers
(`declared_in_source`, `dynamically_exported`) and the three predicates
consumers actually want (`in_source_surface`, `in_exported_public_api`,
`in_exported_public_header_api`), plus the two event guards
(`source_removal_supported`, `export_only_loss`). Every predicate is
evidence-first and falls back to the legacy `Visibility` comparison when
there is no evidence, which is what makes a pre-v46 snapshot produce
bit-for-bit identical findings.

**D4. A guard states which question it asks.** All 39 sites were audited.
The source-axis ones — `diff_namespaces`, where the emitted event is a
*source* removal — moved to `in_source_surface`. The rest genuinely meant
the conjunction and now call `in_exported_public_api`, which is
behaviourally identical to the comparison it replaces. The point of
converting a site whose behaviour does not change is that the question is
no longer inferred from context.

**D5. A removal requires positive evidence that the declaration is gone.**
Not merely that it left one index. `source_removal_supported` suppresses a
removal event only on a positive "the new side still declares this";
unknown leaves the pre-existing answer standing, because absent evidence
must not manufacture a removal *or* suppress a real one.

**D6. The export axis gets its own finding, not a softened removal.**
`FUNC_EXPORT_REMOVED_STILL_DECLARED` / `VAR_EXPORT_REMOVED_STILL_DECLARED`,
both BREAKING, replace `FUNC_REMOVED`/`VAR_REMOVED` when both sides carry
evidence that the export went away and the declaration did not. The
verdict is deliberately unchanged: a consumer recompiling against the new
headers is fine, an already-linked one resolves the symbol at load time
and will not find it. Only the claim changes.

**D7. Schema v46 is additive and does not narrow comparability.** The
ADR-050 comparability contract does not gate on `schema_version`, and
neither D5's guard nor D6's kinds fire without positive evidence on the
side they read, so a pre-v46 side compared against a v46 side degrades to
the pre-fix answer rather than to a wrong one.

## Consequences

Accepted, and stated rather than hidden:

- `diff_namespaces`' source-surface index is **wider** on a v46 snapshot:
  it now admits a header-declared declaration that is not exported. That
  is the fix, and it is symmetric across both sides, so it removes
  findings far more often than it adds them. It can add one — a
  header-declared, never-exported inline genuinely deleted from the new
  headers is now reported, which is a correct source break that was
  previously invisible.
- A run that previously reported `FUNC_REMOVED` for a lost export reports
  a differently-named BREAKING kind. A suppression rule keyed on
  `func_removed` for such a symbol needs the new kind added. Both new
  kinds map onto the existing `func_removal`/`var_removal` dedup
  categories, so cross-detector deduplication is unaffected.
- The facts are produced only by the header-AST backends and the
  export-table synthesis paths. DWARF- and PDB-derived declarations carry
  neither and keep the legacy behaviour exactly.

## Alternatives rejected

**Ignore export changes for inline functions.** This was the obvious
narrow fix and it is wrong: a disappearing dynamic export breaks an
already-linked binary whether or not the function is inline.

**Add a fourth `Visibility` member.** It would make the ordinary
declared-and-not-exported state representable, but it keeps one field
answering two questions — every existing `== PUBLIC` guard would still be
ambiguous, and the "we could not tell" state would still have nowhere to
live.

**Patch `diff_namespaces` alone.** The reproducing site is not the only
one; leaving the same ambiguity at the other thirty-odd guards is what
`AGENTS.md`'s "fix the cause, not the instance" rule forbids.

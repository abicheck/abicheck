---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# `Function.elf_binding`/`Variable.elf_binding` (and the pre-existing `elf_visibility` it mirrors) collapse mixed bindings across symbol-versioned aliases sharing one bare name — fixed 2026-10-12 (Codex review, fresh evidence)

Index: [Known gaps](../known-gaps.md)

**Status (closed 2026-10-12): CLOSED.** Every ELF reader that compared a binding, visibility, size, COMMON or UNIQUE state, origin, or presence per bare name now pairs symbol-versioned aliases. Nothing under `abicheck/` reads `ElfMetadata.symbol_map` anymore, and `tests/test_elf_version_alias_binding.py::test_no_new_code_reader_of_the_collapsed_symbol_map` keeps it that way.

**What changed.** Reproduced first: a two-version library built with `.symver` (`foo@VER1` WEAK, `foo@@VER2` GLOBAL in `.dynsym` order, so the changed alias is the non-last entry) went GLOBAL→WEAK on `foo@@VER2`, and `abicheck compare` exited 0 with no finding for `foo`.
- `model/elf_facts.py`: `ElfMetadata.symbol_aliases` keeps every versioned entry per bare name. `unambiguous_symbol(name)` returns the symbol only when all aliases agree on binding and visibility, otherwise None. `symbol_map` is unchanged, for external callers that want one entry per name.
- `compare/elf_alias_pairs.py` (new): `pair_elf_aliases` pairs old and new aliases by version tag. One entry per side pairs as before. A 1:1 version rename pairs only when both aliases share `is_default`. Any other unmatched alias is recorded with `record_declined`, so it appears in the report's declined list and never vanishes.
- Symbol diff (`diff_platform_elf_symbols.py`): `_diff_elf_symbol_metadata` pairs aliases for the per-symbol checks; the COMMON check and the gained-GNU_UNIQUE check look at every alias.
- Other readers converted (`diff_platform.py`): `_diff_tls_symbols` (`TLS_VAR_SIZE_CHANGED`), `_diff_protected_visibility` (`PROTECTED_VISIBILITY_CHANGED`), and the RTTI visibility/version loop in `_diff_vtable_identity` (`VTABLE_SYMBOL_IDENTITY_CHANGED`).
- Leaked-dependency check (`diff_platform_elf_dynamic.py`): a removed or added alias is matched by version tag, so a surviving bare name no longer hides a removed `SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED`.
- Layout thunk check (`diff_elf_layout.py`): name presence now reads `symbol_aliases`; the result is identical, and the last `symbol_map` read in the code is gone.
- Declarations: `_populate_elf_visibility` and `backfill_missing_elf_binding` take values from `unambiguous_symbol`. On load, a declaration whose aliases disagree has its binding and visibility cleared, so a snapshot written by an older last-wins writer does not keep the wrong value.
- `workflows/dump/cache/salt.py` bumped to v36, since declaration output changed. A warm cache would otherwise replay the last-alias values.

**Verified.** `tests/test_elf_version_alias_binding.py` (38 tests):
- A table covers each converted reader (TLS size, protected visibility, COMMON, UNIQUE, RTTI visibility, leaked removal) with the change on the non-last and on the last alias. On the first-fix commit (`98314ad`) the non-last cases for TLS, protected, COMMON, RTTI and leaked fail; the last-alias controls pass.
- Declined leftovers, the `is_default` rule for renames, and the reader guard fail on `98314ad` and pass now.
- A real gcc-built library is dumped, saved, loaded through the storage codec, and compared. Alias versions survive the round trip, and `foo` is reported.
- A stored snapshot with a disagreeing declaration comes back with binding and visibility unset, while an agreeing one keeps its value.
- Single-version libraries are unchanged, including a version-tag bump on a leaked dependency symbol (name presence still decides); the existing ELF, binding, visibility and diff-platform test files pass (1,813 tests before the table and round-trip additions).

**Residual by design.**
- `symbol_map` remains for external callers and is documented as one entry per bare name.
- A finding still names the bare symbol, so `foo: global → weak` does not say which alias changed. Carrying the version in a finding is a report-field change and needs its own ADR.
- Declines are accounting only and never change a finding. An ambiguous name is declined only by a detector that judged it.
- Verdict impact: `TLS_VAR_SIZE_CHANGED` defaults to `BREAKING`, so a size change on a hidden alias of a multi-alias library now fails where it was hidden. The other newly reachable kinds (`PROTECTED_VISIBILITY_CHANGED`, `VTABLE_SYMBOL_IDENTITY_CHANGED`, `SYMBOL_BINDING_BECAME_UNIQUE`, `SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED`) default to `COMPATIBLE_WITH_RISK`; `COMMON_SYMBOL_RISK`, `SYMBOL_BINDING_CHANGED` and `SYMBOL_ELF_VISIBILITY_CHANGED` default to `COMPATIBLE`.


**Historical analysis (the state before the fix, kept for context).** `ElfMetadata.symbol_map` was a `name -> ElfSymbol` dict
built from `elf.symbols`, last-entry-wins; `elf_metadata.py`'s own parser
deliberately never embeds a version suffix in `ElfSymbol.name` (pyelftools
doesn't decode `@@`/`@` into the name either — see the `_pyelftools_exported_symbols`
comment), so two legally distinct versioned definitions of one exported
name — e.g. a `GLOBAL` `foo@GLIBC_2.2` and a `WEAK` `foo@@GLIBC_2.14` —
collapse to a single dict entry, and `_populate_elf_visibility` (which both
fields share) reads only that survivor. A `Function`/`Variable` whose
`mangled` matches such a bare name therefore reports whichever binding
happened to parse last, not "the" binding — and if that happens to be
`WEAK` while an older, still-live version was `GLOBAL`, a `binding: weak`
suppression rule (`suppression.py`) could match a removal that, from the
`GLOBAL` version's perspective, is a real break. The first pass did not fix this: a correct
fix needs `symbol_map` (or a sibling index) to preserve every versioned
entry per bare name rather than collapsing to one — a change with several
other consumers beyond `elf_visibility`/`elf_binding` (`dumper_elf_symbols.py`'s
own callers, `diff_versioning.py`'s existing version-aware machinery, which
already correlates symbols against `versions_defined`/`versions_required`
through a different path) that deserves its own scoped design rather than a
drive-by change to a cached property several modules depend on today. Per
this file's own "known gaps over risky reactive patches" convention.

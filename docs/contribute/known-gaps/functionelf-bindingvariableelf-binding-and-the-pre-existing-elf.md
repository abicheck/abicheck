---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# `Function.elf_binding`/`Variable.elf_binding` (and the pre-existing `elf_visibility` it mirrors) collapse mixed bindings across symbol-versioned aliases sharing one bare name — investigated, not fixed (Codex review, fresh evidence)

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-12): PARTIAL.** Fixed for the ELF symbol diff and the declaration `elf_binding`/`elf_visibility`; the remaining `symbol_map` readers are still open (see the last section).

**What this pass changed.** Reproduced first: a two-version library built with `.symver` (`foo@VER1` WEAK, `foo@@VER2` GLOBAL in `.dynsym` order, so the changed alias is the non-last entry) went GLOBAL→WEAK on `foo@@VER2`, and `abicheck compare` exited 0 with no finding for `foo`.
- `model/elf_facts.py`: `ElfMetadata.symbol_aliases` keeps every versioned entry per bare name. `unambiguous_symbol(name)` returns the symbol only when all of its aliases agree on binding and visibility, otherwise None. `symbol_map` is unchanged and still last-wins.
- `compare/elf_alias_pairs.py` (new): `pair_elf_aliases` pairs old and new aliases by version tag. One entry per side pairs as before; a version renamed 1:1 pairs; any other leftover is skipped rather than matched to an arbitrary alias.
- `diff_platform_elf_symbols.py`: `_diff_elf_symbol_metadata` compares the pairs from `pair_elf_aliases` instead of `symbol_map` items.
- `extract/elf_symbol_classify.py` (`_populate_elf_visibility`) and `storage/snapshot_load_normalization.py` (`backfill_missing_elf_binding`): a declaration takes binding and visibility from `unambiguous_symbol`, so a mixed-alias name leaves them unset instead of inheriting the last alias.

**Verified.** `tests/test_elf_version_alias_binding.py` (17 tests): binding and visibility changes on either alias are reported (fail before the fix, pass after); pairing by version tag gives the same result for any `.dynsym` order; single-version libraries report exactly one change as before; declarations inherit values only when their aliases agree; a real gcc-built library (`dump` then `compare`) reports `foo` as SYMBOL_BINDING_CHANGED.

**Still open (why PARTIAL).**
- `_diff_tls_symbols`, `_diff_vtable_identity` (`diff_platform.py`), `_diff_leaked_dependency_symbols` (`diff_platform_elf_dynamic.py`) and the layout reads in `diff_elf_layout.py` still use `symbol_map`, so they can hide a change on a non-last alias. Not audited alias by alias in this pass.
- A version renamed on one side with several unmatched aliases on each side is skipped, not reported. A reporting path for that ambiguity needs its own ChangeKind decision.
- COMMON and GNU_UNIQUE checks in `_diff_elf_symbol_metadata` still read `symbol_map`.

The mixed-alias declaration rule treats an absent name and a mixed name the same (both None), which matches how the code already handled an absent name.

`ElfMetadata.symbol_map` is a `name -> ElfSymbol` dict
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
`GLOBAL` version's perspective, is a real break. Not fixed here: a correct
fix needs `symbol_map` (or a sibling index) to preserve every versioned
entry per bare name rather than collapsing to one — a change with several
other consumers beyond `elf_visibility`/`elf_binding` (`dumper_elf_symbols.py`'s
own callers, `diff_versioning.py`'s existing version-aware machinery, which
already correlates symbols against `versions_defined`/`versions_required`
through a different path) that deserves its own scoped design rather than a
drive-by change to a cached property several modules depend on today. Per
this file's own "known gaps over risky reactive patches" convention.

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

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** model/elf_facts.py:238-244 symbol_map is still `{s.name: s for s in self.symbols}`, last entry wins, which collapses versioned aliases.

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

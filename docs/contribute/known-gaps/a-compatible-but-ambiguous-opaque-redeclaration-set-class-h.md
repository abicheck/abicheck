---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# A compatible-but-ambiguous opaque redeclaration set (`class H; struct H;`) that later gains a same-key COMPLETE definition still produces a false `SOURCE_LEVEL_KIND_CHANGED` — investigated, not fixed (Codex review, PR #719, fourth follow-up round on this same area)

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** grep for kind_ambiguous in abicheck/ finds nothing, so RecordType has no provenance for an ambiguous forward-declaration kind.

The
earlier "keep the definition's own kind" fix (this same file, above)
deliberately never applies `dumper_clang.py`'s canonicalized opaque
`override_kind` to a record that survives as a COMPLETE definition — the
definition's own real, unmodified `_record_kind()` always wins, which is
correct in isolation (a real kind change on the definition itself must
never be hidden). But this means an identity whose opaque forward decls
were genuinely ambiguous (`class H;` AND `struct H;`, both present,
canonicalized to the fixed `"struct"` spelling per the kind-stability fix
above) reports "struct" in an old snapshot with no definition, while a
new snapshot adding a same-key `class H {...};` definition reports the
definition's real "class" — a spurious kind-change finding even though
"class" was always one of the two already-compatible, already-declared
keys. Reproduced directly against `_ClangAstParser`. **Not fixable at
the extraction layer**: an opaque snapshot has no way to know in advance
which of the ambiguous, compatible keys a LATER definition will use, so
no fixed canonicalization choice can match every possible future
definition. The generic comparison (`diff_types_abicc_parity.
_diff_type_kind_changes()`, shared across all producers) does a plain
`t_old.kind != t_new.kind` check with no notion of "this kind was
extracted from a genuinely ambiguous, unresolved forward-decl set" —
and correctly so, since blanket-suppressing every class↔struct
transition would hide the genuine, intentional ones this detector
exists to catch. Closing this properly needs new PROVENANCE carried on
`RecordType` itself (e.g. a `kind_ambiguous`-shaped field, schema-
versioned, populated by both header backends, read by the diff layer to
skip exactly this one shape of transition) — a cross-cutting model
change touching `model.py`, both backends, serialization, and the
detector, not a scoped fix reactive to one review comment, and
`dumper_clang.py`'s `parse_types()` has already been revised four times
in this same PR session for adjacent findings in this exact area. Filed
here per this file's own "known gaps over risky reactive patches"
convention rather than attempted under continued review pressure.

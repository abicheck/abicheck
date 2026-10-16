---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Linkage-blind removal — attempted twice, reverted twice. The evidence keeps proving something adjacent to the invariant

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): NEGATIVE_RESULT.** Records two reverted demotion attempts, which should not be re-attempted. The comdat sub-item is still open: COMDAT collection remains opt-in (build_evidence.py:426, env_flag ABICHECK_COLLECT_COMDAT), and adapters/compile_db.py has no output/-o handling (grep finds no 'output').

A symbol vanishing from
the export table is reported as `func_removed` (and, on the same symbol,
`func_deleted_elf_fallback`) regardless of its *linkage*, so a weak
vague-linkage export reads as a hard break. The demotion's invariant is
*"every consumer already emitted its own copy"*, and both attempts
established something else:

1. **`Function.is_inline`** proves the declaration's inline *linkage*, not
   that a definition exists. Verified against real clang: `inline int f();`
   yields `inline=True, has_body=False`. This stays true after the
   implicit-inline fix (`extract/headers/clang/inline_semantics.py`), which
   widened the field to cover `constexpr`/`consteval`, in-class definitions
   and in-class `= default` so the two backends agree: those are all still
   *linkage* facts about the library's own declaration, and none of them
   says a consumer emitted a copy — which is the shared shape this entry
   exists to name. Read it as "may have vague linkage", never as "every
   consumer already has its own definition".
2. **COMDAT-group membership** proves the *library* used vague linkage, not
   that its *consumers* did. `extern template` is the counterexample, and it
   is ordinary code: a public header carrying `extern template struct
   Box<int>;` tells every consumer TU **not** to instantiate, while the
   library's own explicit instantiation still emits a weak COMDAT
   definition. Verified against g++ — the library object has
   `_ZNK3BoxIiE3getEv` inside a COMDAT group while the consumer object has
   it as `NOTYPE GLOBAL UND` with an empty COMDAT set. Dropping that export
   breaks the consumer, and the predicate demoted it (Codex review).

The shared shape is worth naming, because it is what a third attempt will
hit too: a fact about the **library's own build** was read as a fact about
**its consumers**. Nothing in two library snapshots, and nothing in one
library's object files, distinguishes "the consumer emitted a copy" from
"the consumer holds an undefined reference". Only consumer-side evidence, or
a header fact recording `extern template` (which castxml does not expose —
checked through 0.7.0), can separate them.

**Kept, because it is sound and answers a real question:**
`buildsource/comdat_groups.py` — an ELF `SHT_GROUP`/`GRP_COMDAT` parser
(byte-order correct, ELF-only, degrading to diagnostics on unreadable
objects) and its `BuildEvidence.comdat` collection. It answers "did *this
build* emit this symbol vaguely", which is genuinely useful and was already
reserved vocabulary in `graph_facts.py` (`comdat_group`) for a future
linker-artifact extractor. It is simply not sufficient for the demotion.

**Separately open, and independent of the above:** the L3 collection path
does not deliver usable object paths. `CompileUnit.output` is a label
normalized *for persistence*, not a path — home paths redacted to `~/...`
(ADR-032 D7), Ninja/Make/Bazel outputs relative to `CompileUnit.directory` —
and `CompileDbAdapter` never assigns it at all, discarding the compile
database's `output` field and `-o` alike. **Half-closed on the reading
side:** `build_evidence._resolved_object` now expands `~` and joins a
relative label onto the unit's own `directory`, and skips a label naming no
file that exists, so an adapter that *does* record an output is readable
from outside the build directory. That also makes the scan conservative
where it used to be destructive: a build whose objects resolve to nothing
leaves `comdat` untouched rather than replacing a scan loaded from an
existing pack with an empty, unresolvable one, and a fresh scan that
established nothing never displaces one that did. **Still open:** the
producing side — `CompileDbAdapter` recording an output at all, and every
adapter keeping the raw resolved path alongside the redacted label, so a
pack collected on one machine can be scanned on another. Until then the
scan is opt-in (`ABICHECK_COLLECT_COMDAT=1` at `inline.py`'s call site):
parsing every object's symbol table is real I/O, and no detector consumes
the result.

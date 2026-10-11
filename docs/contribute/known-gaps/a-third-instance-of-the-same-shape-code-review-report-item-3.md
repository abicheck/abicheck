---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# A third instance of the same shape (code-review report item 3): demoting a stdlib closure instantiation as "unnameable" — attempted, reverted

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): NEGATIVE_RESULT.** Records a reverted attempt to demote stdlib closure instantiations as unnameable. The same consumer-side evidence gap as entry 13 remains, so do not re-attempt.

A stdlib/runtime template instantiated over a caller-
supplied lambda (e.g. `std::once_flag::_Prepare_execution<...Widget::
run()::{lambda()#1}...>`, from a real `std::call_once` guard) mangles to
a symbol whose closure-ordinal encoding is per-translation-unit and
compiler-ordering dependent, so it seemed unconditionally safe to demote
in `surface.classify_change_surface`: "no consumer's *source code* could
ever name this exact template argument, so there is no possible
external caller to break." That reasoning is the identical mistake
the linkage-blind-removal entry above already names, just one layer
removed: *source-level nameability* is not *binary/ABI compatibility*.
A consumer's own object code never has to name the symbol in source —
the SAME template, instantiated from the SAME public header over its
own local lambda, produces the IDENTICAL mangled symbol in the
consumer's own translation unit via vague/weak linkage, and that
consumer can depend on the library's copy being the one that resolves.
A two-snapshot comparison has no way to rule that out, for exactly the
reason the entry above states: "nothing in two library snapshots...
distinguishes 'the consumer emitted a copy' from 'the consumer holds an
undefined reference'." Reverted rather than shipped (Codex review,
two findings — the unsoundness above, and separately that the fix was
dead code for its own ELF-only motivating case:
`post_processing.FilterNonPublicSurface.run` returns unmodified changes
before ever calling `classify_change_surface` when neither side's
surface is resolvable). Closing this for real needs the same
consumer-side evidence the linkage-blind-removal entry says is missing
— not a cleverer read of the mangled name alone.

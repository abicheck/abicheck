<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **`VIRTUAL_METHOD_ADDED` no longer silently drops coverage when
  `TYPE_VTABLE_CHANGED` has no evidence to act on.** `diff_cxx_rules.
  virtual_method_addition` previously deferred to `TYPE_VTABLE_CHANGED`
  whenever the two sides' raw `vtable` arrays merely differed, without
  checking that the sibling detector would actually fire — the two were
  coupled only by a docstring's claim, in two separate files, with no
  executable link. Wherever that claim didn't hold (the owning class's own
  virtual functions, size, and virtual bases all read identically on both
  sides), neither detector reported the added virtual method at all. Both
  detectors now share one predicate (`compare.vtable_evidence.
  vtable_transition_is_evidenced`), and `virtual_method_addition` falls
  through to its own override check instead of assuming.

### Fixed

- **A pure rename of a template-TEMPLATE parameter no longer changes a
  function template's `EntityId`.** A non-type parameter dependent on a
  preceding template-template parameter (e.g.
  `template<template<class> class TT, TT<int>* N>`) had its declared type
  canonicalized only against preceding *type* parameter names, so
  renaming `TT` to `UU` still fingerprinted as a different overload.
  Fixed by canonicalizing against both type and template-template
  parameter names uniformly.
- **`qualified_name_segments._walk_rewrite_strings` no longer silently
  drops a rewrite to a frozen dataclass's `init=False` field.** Such a
  field can be independently populated (e.g. in `__post_init__`) with
  content that itself needs rewriting, but `dataclasses.replace` cannot
  accept an `init=False` field, so its rewritten value was computed and
  then discarded — most visibly when no `init=True` field on the same
  object changed, leaving the field stale indefinitely. Fixed by applying
  a changed `init=False` field via `object.__setattr__`, after `replace`
  rebuilds the `init=True` fields.

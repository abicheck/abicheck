### Fixed

- **A non-type template parameter's own name is now canonicalized against
  a pure rename too.** `decltype(N)` for a preceding `int N` spells the
  dependent expression using `N`'s own name literally; renaming `N` to
  `M` previously still changed a function template's `EntityId`.
- **The template-parameter rename canonicalization no longer corrupts an
  already-generated marker when a later parameter happens to be named
  `type`.** Applying one substitution per parameter name sequentially
  let a later parameter literally named `type` rewrite the `"type-param-N"`
  token an earlier parameter's own rename had already produced. Fixed by
  canonicalizing all parameter names in a single combined pass, which
  never re-scans its own replacement text.

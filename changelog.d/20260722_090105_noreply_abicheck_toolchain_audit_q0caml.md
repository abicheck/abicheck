### Fixed

- **Hidden-friend surface scoping no longer conflates same-leaf-name classes
  in different namespaces.** `PublicSurface.origin_by_key` is keyed by the
  deliberately-bare `RecordType.name`, so `pub::Foo` (public) and `priv::Foo`
  (private) previously merged into one origin (public wins, conservative),
  wrongly keeping a hidden friend whose *specific* owner is `priv::Foo`.
  New `PublicSurface.origin_by_qualified_key` indexes by
  `RecordType.qualified_name`/`EnumType.qualified_name` when present, so an
  owner identity resolved from castxml's `befriending` attribute or clang's
  friend-scope walk is matched exactly before falling back to the ambiguous
  bare-name path.
- **The C++20 dialect detector now recognizes a requires-expression or
  requires-clause split across physical lines with no backslash
  continuation.** The per-logical-line scan only joined backslash-continued
  lines, so `requires` at the end of one line and its `{`/`(`/constraint on
  the next were never seen together — a common formatting style for
  parameterless requires-expressions. The scan now looks ahead (bounded) past
  a bare trailing `requires` to the next non-directive line.

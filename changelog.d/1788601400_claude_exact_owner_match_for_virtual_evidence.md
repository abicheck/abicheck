### Fixed

- `diff_vtable_layout._is_polymorphic`'s retained-virtual-`Function`
  positive-evidence path (added this session) now matches a function's
  owning class by *exact* qualified identity
  (`diff_types_vtable._owned_virtual_signatures_for_record`) instead of
  the eager namespace-suffix matching `diff_types_vtable
  ._owned_virtual_signatures` uses for its own suppression-oriented
  purpose. The eager variant is safe only when over-inclusion just keeps
  a finding; here a match becomes an unconditional affirmative `True`, so
  an unrelated class sharing only a leaf name in a different namespace
  (`ns1::Foo` vs. `ns2::Foo`) could otherwise fabricate polymorphism.

### Fixed

- **Direct-clang vtable reconstruction now translates a newly-added
  template default through renamed positional parameters.** A dependent
  default's raw spelling names an earlier parameter of the SAME
  declaration; the previous merge fix could adopt a default from a
  redeclaration that ALSO renames its parameters (e.g. `template<class T,
  class U> struct A;` followed by `template<class X, class Y=X> struct
  A {...};`), carrying the renamed text (`"X"`) forward while the tracked
  names index still held the original names (`"T"`, `"U"`) — so the
  dependent-default substitution silently failed to find `"X"` there,
  mis-indexing a specialization and leaving an inherited vtable invisible.
  Fixed by translating a newly-adopted default's dependent reference
  through the contributing declaration's own positional name list into the
  first declaration's name at that same position before merging it in.

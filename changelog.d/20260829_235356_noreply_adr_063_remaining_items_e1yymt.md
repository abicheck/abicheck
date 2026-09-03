### Fixed

- **A qualified member-template lookup (`S::template N<int>()`) is no
  longer canonicalized as if it referenced a same-spelled template
  parameter.** A previous fix excluded a plain qualified name (`S::N`)
  and a member-access expression from this rename-blind substitution,
  but a qualified member-template's own disambiguated form separates the
  name from its `::` qualifier with the `template` keyword and a space,
  which the existing exclusion didn't reach — so renaming an unrelated
  template parameter that happened to share the member template's name
  could still change a function's computed identity even though the two
  declarations were otherwise byte-for-byte identical.

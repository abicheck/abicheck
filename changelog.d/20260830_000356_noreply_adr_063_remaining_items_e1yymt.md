### Fixed

- **A user-defined literal suffix (`'x'_tag`) is no longer canonicalized
  as if it referenced a same-spelled template parameter.** The
  rename-blind substitution used to build a function's identity already
  excluded quoted literal contents, but a user-defined literal suffix
  sits immediately outside the literal's own quoted span (with no space,
  per the language grammar) rather than inside it, so it wasn't covered
  by that exclusion — renaming an unrelated template parameter that
  happened to share the suffix's spelling could still change a
  function's computed identity even though the two declarations were
  otherwise identical.

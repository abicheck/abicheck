<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **`hidden_friend_added`/`hidden_friend_removed` now fire for an
  inline-only friend that keeps the same mangled symbol.** An in-class
  `friend` declaration pulled out to file scope (or vice versa) can
  preserve its mangled name across versions, since a hidden friend already
  mangles under its enclosing namespace rather than the class. Previously
  `diff_inline_hidden_friends` (`abicheck/diff_hidden_friends.py`) skipped
  any mangled key present on both snapshots outright, and the public-symbol
  pairing that does check for this transition never runs at all when the
  function is `Visibility.HIDDEN` (the common case for an inline-only
  friend) — so the transition was silently dropped on both paths. The
  same-key case is now checked too, unless it was already covered by the
  public-symbol pairing.
</content>

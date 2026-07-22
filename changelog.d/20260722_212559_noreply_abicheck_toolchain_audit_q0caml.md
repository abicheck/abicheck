<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **The abbreviated function template detector no longer mistakes
  `decltype(auto)` for a C++20 parameter.** `decltype(auto)` (valid since
  C++14) puts the bare keyword `auto` directly inside a `(`, the same
  textual position as a genuine abbreviated parameter's enclosing `(` —
  but it is `decltype`'s own argument, not a parameter list at all. A
  header using it was force-parsed as C++20 even when its only other
  C++20-looking ingredient was an otherwise-harmless
  `concept`-as-type-name shadow, where `concept` becomes a keyword and
  the header fails. `decltype(...)`'s own `(` is now excluded the same
  way a generic lambda's parameter list already was.
</content>

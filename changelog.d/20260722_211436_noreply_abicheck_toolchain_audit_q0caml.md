<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **`consteval`/`constinit` detection no longer fires when either word
  is shadowed as a pre-C++20 type name.** The "followed by an
  identifier-starting token" check alone can't distinguish a genuine
  specifier from a header that instead declares a *type* literally named
  `consteval`/`constinit` (`struct consteval {};`) and later references
  it followed by another decl-specifier or cv-qualifier (`consteval
  const *p;` — legal pre-C++20, since decl-specifier order is flexible)
  — the textual shape is identical to a genuine declaration. Once a
  header is confirmed to declare either word as a type name anywhere,
  every bare occurrence in it is now treated as ambiguous and skipped,
  mirroring the existing `concept`-as-type-name shadow check.
</content>

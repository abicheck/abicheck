<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **`consteval`/`constinit` detection no longer fires on a pre-C++20
  header using either word as an ordinary identifier.** Neither was a
  reserved word before C++20, so `int consteval;`/`int constinit;`
  (declaring a variable with that name) is legal pre-C++20 syntax. The
  previous unconditional bare-keyword match forced `-std=gnu++20` on such
  a header, where the identifier is no longer usable — actively breaking
  a header that previously parsed fine. Detection now requires a positive
  lookahead for whitespace then another identifier-starting token,
  distinguishing a genuine specifier (`consteval int f();`) from the
  ordinary-identifier shape (the keyword directly followed by
  `;`/`,`/`=`/`)`/`[`).
</content>

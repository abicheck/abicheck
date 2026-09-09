### Fixed

- The multi-variant ambiguity error told users to pass `--old-variant` /
  `--new-variant`, spellings this release retires with no alias — so
  following the tool's own remediation immediately failed again with a
  usage error (exit 64). It now names the live side-scoped spelling, with a
  worked example drawn from the package's own declared ids
  (`... -- pass an explicit variant id to select one (e.g. --variant
  old=gcc13)`). A package declaring *zero* variants gets the same clear
  error with no example rather than an `IndexError`.

  Generalized rather than patched at the one string: the regression test
  extracts every `--flag` token from the message a real `compare`
  invocation produces and asserts each is an option that command actually
  accepts, across five sibling error paths in the variant family. Bug class
  `cli_surface.retired_spelling_in_remediation`; the repo-wide remainder is
  recorded in `docs/contribute/known-gaps.md`.

### Fixed

- The multi-variant ambiguity error told users to pass `--old-variant` /
  `--new-variant`, spellings this release retires with no alias — so
  following the tool's own remediation immediately failed again with a
  usage error (exit 64). It now names the live side-scoped spelling, with a
  worked example drawn from the package's own declared ids, pointed at the
  operand that is actually ambiguous:

  ```
  <pkg> declares 2 variant(s) (['gcc13', 'gcc14']) -- pass an explicit
  variant id to select one (e.g. --variant new=gcc13)
  ```

  A package declaring *zero* variants gets the same clear error with no
  example rather than an `IndexError`.

  The example names the correct side because the engine no longer names a
  flag at all: `materialize_release_variant_artifacts` now raises the typed
  `AmbiguousVariantSelectionError`, carrying the declared ids, and the CLI
  front end that resolved both operands appends the `old=`/`new=` example —
  the same engine-error/CLI-wrapper split `AmbiguousLibraryMatchError`
  already uses. The new class subclasses `ValueError`, so existing
  `except ValueError` handlers around variant resolution are unaffected.

  Generalized rather than patched at the one string: the regression test
  takes the message's own example, **re-invokes `compare` with it**, and
  asserts the ambiguity is actually resolved — parametrized over which side
  is ambiguous, across five sibling error paths in the variant family. Bug
  class `cli_surface.retired_spelling_in_remediation`; the repo-wide
  remainder is recorded in `docs/contribute/known-gaps.md`.

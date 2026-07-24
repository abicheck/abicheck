<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

<!--
### Added

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Changed

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Deprecated

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Removed

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
### Fixed

- **C++20 structural detection now ignores `#if 0`/`#if false` disabled
  regions.** A pre-C++20 compatibility stub kept around but disabled
  (`#if 0\nstruct consteval {};\n#endif`) was previously still seen by the
  `consteval`/`constinit`/`concept` shadowed-type-name scan, which then
  treated a genuine active declaration elsewhere in the same header as
  ambiguous and skipped C++20 detection entirely. Conversely, a genuine
  `consteval`/`constinit`/`concept`/`requires` construct written *only*
  inside a disabled `#if 0` block was still picked up by the requirements
  scan itself, wrongly marking an otherwise pre-C++20 header as needing
  C++20. Inactive `#if 0`/`#if false` regions (including nested directives
  and CRLF line endings) are now stripped once, up front, before both
  scans run — while a reachable `#else`/`#elif` arm of the same guard is
  left untouched, so a genuine construct written only there is still
  detected. A further `#elif 0`/`#elif false` arm stays masked exactly
  like the `#if 0` guard before it, rather than being treated as reachable
  the way a genuinely unevaluable `#elif <macro>` condition is. And once
  an `#elif 1`/`#elif true` arm fires, every later sibling arm in the same
  chain is unconditionally unreachable in any build configuration, so
  masking resumes for them too instead of staying lifted for the rest of
  the chain. Separately, a pre-C++20 header declaring a type literally
  named `requires` and using it in a variable template
  (`template<class T> requires value = {};`) is no longer misread as a
  genuine requires-clause — the preceding-template-header positive signal
  alone couldn't distinguish the two shapes, mirroring the existing
  consteval/constinit/concept type-shadow checks.

-->
<!--
### Performance

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Security

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Documentation

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->

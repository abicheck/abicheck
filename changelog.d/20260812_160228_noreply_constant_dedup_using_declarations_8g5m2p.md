<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **castxml no longer double-reports a using-re-exported header constant** —
  `namespace v1 { constexpr T x = ...; } using v1::x;` made castxml emit a
  second, independent declaration for the using-declaration, so
  `AbiSnapshot.constants` carried both `ns::v1::x` and `ns::x` for one real
  declaration and `compare` reported `CONSTANT_ADDED`/`CONSTANT_REMOVED`
  twice for a single change. The version-qualified spelling is now collapsed
  onto its version-stripped alias when both carry an identical value (a
  using-declaration cannot legally re-export a constant under a different
  value), keeping the spelling a consumer of the re-export actually writes.

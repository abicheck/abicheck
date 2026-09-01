<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **`--bundle-facts-library-manifest` accepts a real, versioned library
  filename as a key** — an entry keyed by the literal on-disk filename
  (e.g. `libfoo.so.1`, common for a runtime package with no unversioned
  dev symlink) was previously rejected as "not a library in this bundle",
  since bundle library names are always resolved canonically (`libfoo.so`).
  Manifest keys are now canonicalized the same way before that check, so
  either spelling works; two keys that canonicalize to the same library are
  now a clear error instead of one silently overwriting the other.

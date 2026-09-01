<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Added

- **`compare --old-bundle-facts --bundle-facts-library-manifest`** — give
  individual libraries in a stored-`BundleFacts`-baseline comparison their
  own header root, include path, or compile context instead of the
  comparison's uniform `--header`/`--include`/compile-context flags, for a
  bundle whose libraries don't share one toolchain (e.g. a plain-C++
  library alongside a `-fsycl`/`icpx` DPC++ one). A library not named in the
  manifest keeps the uniform fallback; a manifest entry naming a library
  outside the bundle is a hard error. See `abicheck/bundle_facts_library_
  overrides.py` and [Multi-Binary Releases](https://abicheck.readthedocs.io/en/latest/use/multi-binary/#per-library-headerincludecompile-context-overrides-g38-phase-17).

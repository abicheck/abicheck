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
- **`compare --old-bundle-facts --output-dir` rejects a colliding `-o`/
  `--output` or `--write` path** — a per-library report filename derived
  from `--output-dir` could collide with the primary/secondary report's own
  output path, silently clobbering whichever write ran second. The
  collision is now detected and rejected (exit 64) before any artifact is
  written.
- **`--bundle-facts-library-manifest` rejects an override for a library
  that isn't actually compared** — a manifest entry naming a library
  present in `NEW_INPUT` but absent from `OLD_FACTS` (an added library, or
  vice versa) previously passed validation yet was silently never
  consulted, since the real per-library comparison only matches libraries
  present on both sides. Such an entry is now rejected up front.
- **`--bundle-facts-library-manifest` rejects a versioned alias that names
  the wrong version** — when `NEW_INPUT` carries more than one version of a
  library (e.g. `libfoo.so.1` and `libfoo.so.2`), only one is actually
  selected for comparison; a manifest entry keyed by a *different*,
  non-selected version previously canonicalized to the same bundle key and
  silently applied its override to a file it never named. A versioned
  manifest key must now name the canonical name or the exact file that was
  selected.
- **`compare --old-bundle-facts --output-dir` rejects naming the same path
  as `-o`/`--output`** — when `--output-dir` and the primary/secondary
  output path were the same, previously nonexistent path, the primary write
  created a file there and the following `--output-dir` directory creation
  then raised a raw, unhandled error instead of a clean usage error. This
  exact-path collision is now rejected the same way the per-library-report
  collision already was.
- **`compare --old-bundle-facts --output-dir` rejects an existing non-
  directory path** — an `--output-dir` that already existed as a regular
  file (unrelated to `-o`/`--output`/`--write`) was not caught by the
  collision checks above; the primary report was written before directory
  creation raised a raw, unhandled error. `--output-dir` is now validated
  as a non-file path before any artifact is written.
- **`compare --old-bundle-facts --output-dir` reports directory-creation
  failures cleanly** — any remaining `--output-dir` creation failure (a
  non-directory *parent* path component, a permission error) leaked a raw,
  unhandled `OSError` after the primary report had already been written.
  Wrapped in the same `OSError` → clean CLI error translation the rest of
  this command's output writing already uses.
- **`--bundle-facts-library-manifest` rejects an empty per-library entry**
  — a manifest entry with no override fields (`libfoo.so: {}`) was silently
  accepted but added the library to none of the parsed override maps,
  making it invisible to the matched-library validation above; a
  comparison would silently apply every uniform fallback with no signal
  the requested override was never applied. Such an entry is now rejected
  up front.
- **`--bundle-facts-library-manifest` reports a deeply nested manifest
  cleanly** — a well-formed but sufficiently deeply nested manifest (~1,500
  nested sequences) exhausted Python's own recursion limit inside the
  strict YAML loader, leaking a raw `RecursionError` traceback instead of
  the exit-64 usage error every other malformed manifest input produces.

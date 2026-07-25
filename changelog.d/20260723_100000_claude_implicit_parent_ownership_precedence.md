<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **A declared header's implicitly project-owned parent could be
  overridden by a nested, non-owned `-I` directory** (Codex review,
  PR #624): `_attribute_file` checked `declared_includes` for a
  longest-prefix match *before* checking whether the file fell under a
  declared header's own (implicitly project-owned) parent directory. A file
  under that implicit parent that also happened to fall under a nested,
  external `--include` — e.g. `--header old/include/foo.h` plus `--include
  old/include/sub`, with `foo.h` quote-including `sub/detail.h` — was
  attributed to the external slot and content-hashed, even though it is
  structurally part of the same project directory tree the implicit-parent
  rule exists to exclude. An ordinary internal support-header edit under
  such a nested `-I` could therefore spuriously raise
  `ProfileMismatchError`. The implicit-parent check now runs first, so a
  file's project ownership is determined by its location relative to
  declared headers regardless of which `-I` directory also happens to
  contain it.

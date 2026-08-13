<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **Zero-config Bazel `--sources` scans now populate `BuildEvidence.targets`** —
  the auto-inference path (`buildsource/build_query.py`) previously only ever
  ran `bazel aquery` for a detected Bazel workspace, which never populates
  `BuildEvidence.targets` (only `BazelAdapter`'s `cquery`-processing method
  does), so a plain `dump --sources`/`compare --sources` against a Bazel
  checkout always reported zero resolved targets no matter what the
  workspace contained. A second, best-effort `bazel cquery --output=jsonproto
  deps(//...)` now runs alongside the existing aquery, and both are merged
  through the same `BazelAdapter` call an explicit `--build-info`
  bazel-cquery/-aquery pair already uses. The cquery run is a pure
  supplement — a launcher/timeout/parse failure only appends a diagnostic
  and leaves `targets` empty, never demoting the aquery-derived
  compile/link-unit ingest from `ok`/`partial` to `failed`.

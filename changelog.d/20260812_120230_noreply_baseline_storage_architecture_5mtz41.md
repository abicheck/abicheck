<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`actions/stage-baseline/run.sh`'s Python `zstandard` fallback cleanup**
  no longer aborts the step for a leading-dash `asset-name-template`
  resolution — `rm -f "$asset_name.tmp-payload"` parsed a name like
  `-nightly.tar.zst.tmp-payload` as a run of short options rather than a
  literal filename, and under this script's own `set -euo pipefail` that
  failed the whole step (never writing the `asset-name` output) even
  though the archive itself was built successfully. `'-'` is a legal
  leading filename character on every real filesystem and isn't rejected
  by this script's own newline/CR/path-separator/drive-prefix/`#` guard,
  the same class of gap `publish-baseline.yml`'s upload step already
  guards against for this supported filename case.

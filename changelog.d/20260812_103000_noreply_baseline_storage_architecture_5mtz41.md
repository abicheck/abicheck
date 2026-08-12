<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`protect-committed-baseline.yml`'s protected-path matcher** now reads
  the PR's changed-file list via a NUL-delimited `git diff -z` temp file
  instead of `git diff --name-only`'s ordinary newline-delimited output
  captured into an environment variable — the ordinary form C-quotes any
  pathname containing a control character (a literal newline, tab, or
  quote), which the matcher previously saw as a quoted string instead of
  the real pathname, silently failing to protect a baseline file whose
  name contains one. The glob-to-regex translator's `"**"` segment also
  now matches an embedded newline (`re.DOTALL`), which the temp-file fix
  alone was not sufficient for.
- **`actions/stage-baseline/run.sh`'s zstd Python fallback** now resolves
  `python3`/`python` portably (mirroring the existing convention in
  `action/run.sh`'s own fallback) and reports an actionable error when
  neither a `zstd` binary nor any Python interpreter is available, instead
  of failing opaquely.
- **`publish-baseline.yml`'s existing-profile match guard** now also
  treats an empty resolved profile on either side as a mismatch, closing
  a latent gap where two independently-empty profile strings would
  otherwise compare equal.

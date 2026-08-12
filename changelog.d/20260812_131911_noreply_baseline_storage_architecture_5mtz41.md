<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`actions/stage-baseline/run.sh`'s staging-directory containment
  check** now compares filesystem *identity* (`find ... -samefile`)
  instead of `realpath`-based string comparison, fixing a real
  windows-latest CI failure the string-based version introduced: a
  caller-supplied `baseline-path` arrives as a Windows-style backslash
  path while `mktemp`'s own output is POSIX-style, and Git Bash/MSYS's
  `realpath` (or Python's `os.path.realpath`) does not reliably normalize
  the two to an identically comparable form, so the collision went
  undetected on that platform. Two paths naming the same inode always
  agree regardless of which string form either is spelled in — dropping a
  marker file in the candidate staging directory and asking `find` (the
  same traversal mechanism `tar -C "$BASELINE_PATH"` itself uses) whether
  it's reachable from `baseline-path` sidesteps path-string representation
  entirely.

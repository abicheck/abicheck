<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **The root Action's release-contract baseline-set fallback** now resolves
  a Python interpreter once, up front (`python3` falling back to `python`),
  instead of hardcoding `python3` at its three extraction/resolution call
  sites — a runner where `actions/setup-python` exposes only `python` to
  Git Bash (a real, not hypothetical, Windows shape) previously failed
  every otherwise-valid baseline-set fallback with "command not found".

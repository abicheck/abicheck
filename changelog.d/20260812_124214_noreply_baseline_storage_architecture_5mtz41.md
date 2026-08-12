<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`actions/stage-baseline/run.sh`** now stages the archive it builds in a
  temporary directory outside `baseline-path` and moves it into place
  afterward, instead of building it directly inside the current working
  directory while simultaneously archiving `baseline-path` with `tar -C`.
  When `baseline-path` is the workspace itself (or any directory
  containing the working directory), the previous approach could make
  `tar` see its own output file appear mid-archive — a case GNU tar
  degrades gracefully on this repo's tested version (a skip with a
  warning), but not a behavior guaranteed across every `tar`
  implementation.
- **`action/run.sh`** now re-checks the `baseline-profile`/
  `baseline-target`/`abi-baseline` pairing rules itself (mirroring
  `validate-inputs.sh`'s own copy, per this repo's "keep
  `validate-inputs.sh` and `run.sh` in sync" convention) — previously,
  `baseline-target` set without `baseline-profile`, or either set without
  `abi-baseline`, was silently discarded by a direct `run.sh` invocation
  (bypassing `validate-inputs.sh`), letting a separately-supplied
  `old-library`/`against` run in its place instead of erroring.

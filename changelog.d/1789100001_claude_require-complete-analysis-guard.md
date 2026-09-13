### Fixed

- **`action/run.sh` now rejects the retired `require-complete-analysis`
  input on every mode**, not only under `mode: compare`. The guard's own
  comment described it as "defense in depth for anyone invoking run.sh
  directly", but it sat inside the compare branch, so a direct `run.sh`
  invocation with `mode: dump` and the input set went on to analyse instead
  of failing. The shipped composite action was never affected —
  `validate-inputs.sh` rejects it on every mode before `run.sh` runs — so
  this restores the second layer rather than closing a reachable hole.

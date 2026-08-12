### Added

- **`actions/stage-baseline`, a new composite Action packaging a baseline-set
  directory into a distributable archive** — factored out of
  `publish-baseline.yml`'s own packaging step so a caller publishing through a
  different storage backend can reuse the same suffix-dispatched
  (`.tar.zst`/`.tar.gz`/`.tgz`/`.tar`) archive-encoding logic instead of
  hand-rolling it. `publish-baseline.yml` now calls this Action rather than
  keeping an inline copy.
- **`check-target`/`check-single.yml`/`check-project.yml` now accept and
  forward `expected-project-ref`** to `resolve-baseline`'s wrong-commit
  guard — previously only reachable via a hand-rolled `resolve-baseline`
  step, not through the composed check-target path an `accepted-main` PR
  gate typically uses.

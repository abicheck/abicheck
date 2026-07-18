### Added

- **`abicheck aggregate` — a multi-target CI fan-in gate.** Folds the
  per-target `compare`/`scan` JSON reports produced by a build matrix
  (`abi-report-<target>.json` per leg) into one gate decision:
  `abicheck aggregate --expect linux-x86_64,windows-x86_64 <reports-dir>`.
  Its core invariant is that an expected target with **no** report is
  *unavailable* (unknown), never folded into the verdict as compatible —
  fixing the silent footgun in the previously-documented hand-written
  post-matrix gate, where a target whose build failed before uploading its
  report was dropped and the gate could pass green while a required platform
  was never analyzed. **Findings** (worst ABI verdict over analyzed targets)
  and **coverage** (did every required target report?) are graded as two
  orthogonal conclusions, so a build-infrastructure failure is never reported
  as an ABI regression. Exit code follows `compare`'s 0/2/4 scheme;
  `--on-missing-required {fail,warn}` controls whether incomplete required
  coverage fails the gate. The expected-target set is passed in from the CI
  matrix (`--expect`), not stored as a separate manifest — the matrix stays
  the single source of truth.

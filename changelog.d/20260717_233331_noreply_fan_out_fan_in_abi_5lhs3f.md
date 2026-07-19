### Added

- **`abicheck aggregate` — a multi-target CI fan-in gate.** Folds the
  per-target `compare`/`scan` JSON reports produced by a build matrix
  (`abi-report-<target>.json` per leg) into one gate decision:
  `abicheck aggregate reports/ --manifest abi-targets.json`. Its core invariant
  is that an expected target with **no** report is *unavailable* (unknown),
  never folded into the verdict as compatible — fixing the silent footgun in
  the previously-documented hand-written post-matrix gate, where a target whose
  build failed before uploading its report was dropped and the gate could pass
  green while a required platform was never analyzed. Three axes stay orthogonal
  (ADR-042): **compatibility** (worst verdict, for reporting), **gate** (each
  report's own recorded `severity` decision, *combined* — never recomputed from
  the verdict, so a policy-blocked `COMPATIBLE` still fails and a demoted
  `BREAKING` can pass; reports with no severity block fall back to the legacy
  verdict→exit mapping), and **coverage** (did every required target report?).
  A required-coverage gap is a *coverage* failure at exit `1`, never promoted to
  an ABI-break exit `4`. Exit scheme: `0` pass / `1` coverage gap, an
  addition/quality-only gate block, or a non-verdict per-report failure (e.g. a
  `scan` budget overflow) / `2` source-API break / `4` ABI break / `64` usage. The expected-target set is first-class and explicit — one of
  `--manifest` (a committed source of truth fed to both matrix and gate),
  `--expect`/`--optional`, or an explicit `--discovered-only` opt-out is
  required; a bare `aggregate reports/`, a malformed manifest, or a duplicate
  target id is a usage error. `--on-missing-required {fail,warn}` and
  `--on-unexpected-target {include,warn,fail,ignore}` tune the policy, and
  `--format json` emits a versioned (`aggregate_schema_version`) result with the
  three axes kept separate (including an `unexpected_targets` list). The gate is
  read **fail-closed**: a report whose `severity` block is present but corrupt
  (bad/out-of-range `exit_code`, `blocking` contradicting `exit_code`,
  non-string categories) makes that target *unavailable* rather than reverting
  to the greener legacy path; only an entirely absent gate block legacy-falls-back.
  `scan` reports are read via their own top-level `exit_code`
  (`scan_schema_version`), and when a manifest pins a `head_sha`, a report that
  is missing or mismatches it is treated as unavailable. The `--format json`
  output has a published JSON Schema
  (`abicheck/schemas/aggregate_report.schema.json`, mirrored at
  `docs/schemas/v1/`), and the manifest may carry an optional
  `aggregate_manifest_version` (a newer major is rejected). The root
  `abicheck --help` now groups the verbs by role (core analysis / workflow
  composition / legacy compatibility) so `aggregate` reads as report-level
  composition rather than a sixth binary-analysis peer. See ADR-043 D13.

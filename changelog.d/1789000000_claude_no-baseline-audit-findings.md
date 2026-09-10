### Fixed

- **The audit resolves the project config `compare` discovers.** It
  dispatched before that resolution and never reached it, so an
  auto-discovered `.abicheck.yml` was invisible: a malformed one exited `0`
  where ordinary `compare` exits `64`, and a valid `scope.public: false` was
  dropped — auditing a *different surface* than the same directory's
  `compare` would. Resolved through the same function at the same
  CLI > config > default precedence, and `--config` is accepted rather than
  rejected, since the discovered file is now honored.
- **SARIF says why a gated audit exited.** The coverage ledger reaches the
  `toolExecutionNotifications` array (SARIF's shape for "the run itself was
  limited") and the run's properties, and the exit-code description names
  every contributing axis, instead of publishing a bare `exitCode: 1`.
- **A coverage-gated audit now publishes the ledger that gated it.** Under
  `--contract`, the report kept only the numeric contribution, so even
  `--format json` exited 1 with no way to see which provider, on which side,
  fell short and why. `contract_coverage_failures` is now emitted whenever a
  contract domain was selected — derived from the run's own persisted context
  by the same function the two-sided report uses.
- **`--version old=` is now a usage error** rather than a silently dropped
  label. It was recorded as indistinguishable from the default, which was
  wrong: only the default placeholder is. A bare `--version 1.2`, which
  labels the candidate, keeps working.
- **`--dry-run` validates `--suppress`/`--policy-file` before previewing.** A
  malformed document exits `64` on the real run, so a preview that exited `0`
  approved a run that could not start.
- **The pinned-depth floor now applies to every operand this run parses**,
  not only to a recognized binary. `Module.symvers`, a bare BTF/CTF blob and
  an ABICC Perl dump each become a fresh snapshot that structurally cannot
  carry L3-L5 evidence, yet all three read as "already stored" and were
  exempted: `compare --no-baseline Module.symvers --depth source` reported a
  clean audit with no evidence tiers at all. The two-sided
  `compare a.symvers b.symvers --depth source` did the same, so the rule now
  has one owner (`workflows/input_resolution.side_is_live`) both forms call.
  Only a genuinely serialized snapshot is exempt — in either of its shapes,
  a `.abi.json` file or a directory-backed `ProjectSnapshot` package.
- **`compare --no-baseline` accepts a `ProjectSnapshot` package directory.**
  It is a single artifact — `resolve_input` decodes one into exactly one
  snapshot, and a two-sided `compare` already accepted it — but a blanket
  directory check refused it, so the same stored snapshot was usable as a
  file and rejected in the repository's own storage-v2 form. A release
  *directory of libraries* stays a usage error, naming the fan-out it needs.
- **`compare --no-baseline` now applies the pinned-depth floor when raw
  `--sources`/`--build-info` is given**, even though the artifact operand is
  a stored snapshot. Liveness is a property of the run, not only of the
  operand's path: a run collecting L3-L5 evidence itself can genuinely fall
  short of a pinned `--depth build`/`--depth source`. It exited `0` where the
  equivalent two-sided invocation exited `7`. The predicate has one owner
  now (`buildsource/raw_evidence.py`), shared with `compare`'s own inline-
  collection routing.
- **A gated audit now says which axis gated it.** The exit code is a `max`
  over several orthogonal axes, but only the contract-coverage one was ever
  explained -- so a missed evidence contract exited `7` beside a report that
  mentioned nothing but shallow evidence. Every contributing axis now
  renders a notice in Markdown and appears under `exit_axes` in JSON
  (`exit_axes` in the audit report), read from the same function the exit code is
  folded from.
- **Markdown reports no longer let a finding's own text restructure a
  table.** A detector's description, a demangled symbol, or an extractor's
  error text could contain a pipe, a newline, or a backtick and close a code
  span, end a table row early, or split the table apart. The escaping rule
  `report/comparison_scope.py` already applied to scope tables now lives in
  one shared leaf (`report/markdown_text.py`) that the audit's finding tables
  use too, so the two cannot drift. Generated-input property tests over that
  primitive also found a hole the original had, and then falsified the first,
  too-narrow fix for it: an odd-length run of backslashes immediately before
  a pipe pairs off against the escape being emitted for that pipe, leaving a
  live cell separator and one extra cell in the row.
- **`abicheck compare --no-baseline` now reports the single-build audit's
  findings.** The audit is implemented as a self-compare, and it used to
  assert the resulting change set was empty — an invariant that held until
  the eleven cross-source hygiene checks and the pattern/preprocessor
  pre-scan moved into `compare()`, where they legitimately fire on a
  self-compared snapshot. Every stored-snapshot candidate aborted with an
  unhandled `AssertionError`, and a live binary rendered a report with
  nothing in it. The change set is now partitioned: the comparison half is
  still provably empty (and still enforced, as a raised error rather than an
  `assert`, so the guard survives `python -O`), while the candidate-side half
  is reported under a new `findings[]` block. Findings carry their evolution
  state, which with no baseline is always `persistent` or `not_evaluated`,
  never `introduced`.
- **`compare --no-baseline` now folds `-H`/`--header` into public-header
  provenance**, as a two-sided `compare` always has. Without it every
  declaration resolved to an unknown scope origin, so `exported_not_public`,
  `public_not_exported`, `rtti_for_internal_type` and
  `public_to_internal_dependency` evidence-gated themselves off and reported
  nothing.
- **`compare --no-baseline --contract` is no longer silently inert.** The
  flag was parsed and documented but never forwarded, so the contract-coverage
  ledger it gates on was never populated: a run against a headerless candidate
  exited `0` where the two-sided equivalent exited `1`. A CI job relying on it
  as a gate got no warning that it never ran.
- **`compare --no-baseline` now reads `--sources`, `--build-info`, `--depth`
  and `--dry-run`.** All four were parsed and ignored. `--depth build`/
  `--depth source` is held to the same evidence-contract floor as the
  two-sided path (exit `7`) instead of silently degrading to symbols-only
  evidence and reporting a clean audit; `--dry-run` reports that condition as
  a blocker before any analysis runs. An `old=`-scoped evidence input
  (`--sources old=…`) is now a usage error rather than a silent drop.
- **`compare --no-baseline` now honours `--write FORMAT=PATH` and
  `--include-system-declarations`.** Both were accepted and silently
  dropped — the same class as the three above. `--write` renders from the
  same analysis rather than re-running it (ADR-068 D4), and a secondary
  format outside the audit's supported set is a usage error naming
  `--write`, not `--format`.

- **An option `compare --no-baseline` does not implement is now a usage
  error, not a silent no-op.** "Accepted but never read" caused four separate
  defects on this path, none of which any test could catch, so the rule is
  inverted: every `compare` option is either wired to the audit or named in
  an explicit unsupported table whose message says why, and a test fails if
  any option is neither. Newly rejected rather than dropped: `--used-by`,
  `--used-by-manifest`, `--required-symbol`, `--use-cases`,
  `--post-manifest`, `--env-matrix`, `--diagnostic-comparison`,
  `--old-variant`/`--new-variant`, `--bundle-facts-*`,
  `--since`/`--changed-path`, `--select`/`--select-required`,
  `--output-dir`, `--abi3`, `--budget`, `--severity-preset`, `--pack`,
  `--config`, `--instantiation-manifest`, `--follow-deps`, `--search-path`,
  `--ld-library-path`, `--debug-info` and `--devel-pkg`.

- **A suppressed finding no longer disappears from a `--no-baseline`
  audit.** `compare()` moves a matched finding out of the change set, and
  the audit read only what remained — so a run that detected and hid a
  finding was indistinguishable from a clean one. Suppressed findings are
  now reported as a *disposition*, with the rule that hid each one: a
  `suppressed_findings[]`/`suppressed_count` pair in JSON, a table in
  Markdown, a count in `oneline`, SARIF's native `suppressions` array, and a
  `<skipped>` case in JUnit.
- **`--format junit` no longer fails a build the CLI passed.** Every finding
  was rendered as a `<failure>` while the same audit reported exit `0` —
  hygiene findings are advisory and never gate on their own. Each finding is
  now a passing `<testcase>` (the fact stays visible), and a single
  `exit code` case fails only when one of the orthogonal axes actually gated
  the run, so the JUnit file and the process exit code agree by
  construction.
- **`--write` under a missing parent directory no longer ends in a
  traceback.** It now uses the same writer `-o/--output` does, which creates
  parents and reports a write failure as a clean error.
- **`--dry-run` no longer disagrees with the run it previews.** A pinned
  `--depth build`/`--depth source` on a *stored* snapshot candidate was
  reported as a blocker, while the real run exempts that operand (it
  performs no extraction, so it cannot fall short of a depth) and exits `0`.

- **`compare --no-baseline --depth binary` no longer parses headers.** It
  hand-built its evidence description instead of using the shared resolver, so
  it skipped that resolver's binary-depth clearing rules and could report a
  header-derived finding at a depth documented as symbols-only — diverging
  from the two-sided `compare` on the identical invocation.
- **A failed evidence contract is now visible on `run_outcome.operational`.**
  The exit code was already `7`, but the structured outcome still read `none`,
  telling a report consumer the run was operationally fine.
- **A linker-script candidate is held to the pinned-depth floor.** A GNU ld
  `INPUT(...)` script resolving to a real library was treated as a stored
  snapshot and exempted, so `--depth build` exited 0 where the same library
  named directly exited 7.
- **An unreadable candidate reports a clean error instead of a traceback.**
  Extraction failures are now translated at the CLI boundary the same way the
  two-sided path already translates them.
- **`--version`, `--debug-root` and `--include`'s per-path labels are read;
  `--dump-manifest`, `--probe-matrix`, `--debug-info` and `--devel-pkg` are
  refused.** All were silently dropped: they reach the command under
  destination names produced by option normalization, and the guard added
  earlier in this changelog was keyed on the pre-normalization names, so it
  could never fire for them. A bare `--dump-manifest` was the worst case —
  even an invalid manifest exited 0 while the audit analysed a different
  surface than requested.

### Added

- **`compare --no-baseline --format` accepts `sarif`, `junit` and `oneline`**
  alongside `json` and `markdown`. SARIF and JUnit are findings formats with
  no verdict slot to leave empty, which is exactly what an audit produces, and
  both are how a CI job consumes one. `html` and `review` remain a usage
  error by ruling rather than deferral: both render a comparison (verdict
  badge, old-to-new counts, release recommendation) and an audit has none of
  those — the error now says so and points at `oneline`.

### Changed

- **The audit has a schema identity of its own.** Its JSON carries
  `audit_report_schema_version: "1.0"` and validates against a new published
  `audit_report.schema.json`, rather than stamping the compare report's
  `report_schema_version`. That field identifies a *compare* report, whose
  schema tells consumers to accept any matching MAJOR — so an audit offered
  there was a different document wearing another's identity, and failed that
  schema on two counts: a null `verdict` means "the comparability gate
  rejected this pair" there (and so requires a `reason` an audit must not
  claim), where in an audit it means "no comparison was performed"; and
  `no_baseline` is outside the enum its `selection` field allows.
  `findings[]`, `cross_source_evolution`, `exit_axes` and a candidate-only
  `pattern_preprocessor_scan` block are the audit's own; `changes` remains
  present and always `[]`, so a consumer reading it off any abicheck report
  still finds it.

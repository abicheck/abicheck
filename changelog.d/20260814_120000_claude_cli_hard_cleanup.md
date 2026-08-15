### Removed

- **`compare --verify-runtime` and its inert execution probe are gone.** The
  flag had already been reduced to a documented safety no-op — it never ran
  anything and always reported `attempted=False` — so a caller passing it got
  a flag that silently did nothing. Removed outright along with
  `abicheck.runtime_probe`, the `consumer_runtime_load_failed` `ChangeKind`
  nothing could produce anymore, and the `verify-runtime` inputs on the
  composite Action, `actions/check-target`, and the `check-single`/
  `check-project` reusable workflows. Use the static `--used-by` scanner for
  undefined-symbol corroboration; it answers the same question from the
  binaries' own import/export tables and never executes an analyzed artifact.
  The `runtime_proven` evidence-level vocabulary stays in the report schema so
  an already-published report still reads back correctly.

- **`--gcc-path` / `--gcc-prefix` / `--gcc-option` are removed** from
  `compare`, `dump`, and `scan`. `--compiler` / `--compiler-prefix` /
  `--compiler-option` are now the only spelling — the old names were always
  misleading (each accepts a Clang cross-compiler just as well), and carrying
  both meant a per-invocation conflict resolver whose only correct answer for
  the repeatable option pair was to reject mixing them. `CompileContext`'s
  internal `gcc_*` field names are unchanged, as are the Action's `gcc-path`/
  `gcc-prefix`/`gcc-options` inputs (they now forward to the `--compiler*`
  flags).

- **`--contract-evaluation` is removed** from `compare` and `scan --against`.
  `--contract` was already enough to ask for a contract decision, so the
  standalone switch was a second way to request one thing. `--contract` now
  takes a fourth value, `auto`, for the one case the switch alone expressed —
  evaluate, but let the domain fall through to
  `--scope-public-headers`/`--no-scope-public-headers` and then `.abicheck.yml`
  rather than stating it on the command line. The typed Python API
  (`CompareRequest`/`ScanRequest.contract_evaluation`) is unchanged; it still
  requires the flag and the mode together.

- **`compare --show-impact` is removed.** `--report-mode impact` was documented
  as its exact equivalent (`full` plus the impact table) and is now the one way
  to ask for that table.

- **`aggregate --expect` / `--optional` / `--report-prefix` are removed.**
  The expected-target set is now declared only by `--manifest` or `--run-plan`
  (or waived with `--discovered-only`): an inline id list retyped on the
  command line is exactly the plan-vs-gate drift the shared manifest file
  exists to prevent, and `--optional` was only ever a modifier on `--expect`.
  The report-filename prefix is fixed at `abi-report-`; a report that
  self-identifies a `target_id` never consulted it anyway.

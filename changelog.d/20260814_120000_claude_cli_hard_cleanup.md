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

- **The four per-category `--severity-*` flags are removed** from `compare`,
  `scan --against`, and the composite Action's `severity-addition` input.
  They were hidden CLI duplicates of `.abicheck.yml`'s own `severity:` block,
  which is now their one spelling; a hidden flag that shadows a config key is
  a second way to say one thing, and the config file is the half that
  survives across invocations. `--severity-preset` stays as the visible
  coarse per-run override.

- **`--strict-suppressions`, `--require-justification`, `--public-symbol`,
  `--public-symbols-list`, `--show-redundant` and `--collapse-versioned-symbols`
  are removed** from `compare` and `scan`, for the same reason: each was a
  hidden duplicate of a `suppression:`/`scope:` config key
  (`suppression.strict`, `suppression.require_justification`,
  `scope.public_symbols`, `scope.show_redundant`,
  `scope.collapse_versioned_symbols`), which is now the only spelling.

- **`dump --public-header` and `--public-header-dir` are removed.**
  Declaration provenance (ADR-015) now comes from `-H/--header` itself: a
  file entry tags that header public, a directory entry tags everything
  under it — the same partition `compare` has always applied to its own
  `-H` list. `scan` keeps its own `--public-header-dir`.

- **`dump -p/--build-dir` and `--compile-db`, and `scan --compile-db`, are
  removed.** `--build-info` already takes exactly that operand — a build
  directory, a `compile_commands.json`, or a pre-captured pack — so it is
  now the one flag for it, on every command. When it resolves to a compile
  database and `-H/--header` is given, that database parameterizes the
  header parse the way `-p` used to; `--compile-db-filter` still scopes it.
  One usage error goes away with the flag: a database with no headers is now
  an ordinary L3-only dump rather than "requires -H/--header".

- **`project validate-use-cases --against`/`--against-new` are removed**, and
  the capability they carried is now `compare --use-cases MANIFEST`. A
  manifest validator had grown a second snapshot-diffing surface inside it;
  the attribution belongs where the comparison already happens. The new flag
  resolves each declared use case's entrypoints against *both* sides' source
  graphs and reports which of the comparison's own findings each use case
  reaches, as the report's `use_case_impact` block (schema 2.39) and a
  text/markdown section. Read-only: an unattributed finding is an absence of
  proof, not proof the finding is harmless, so it never moves a verdict or an
  exit code. `project validate-use-cases` still checks a manifest's structure.
  Rejected as a usage error when *no* output the run renders can carry the
  attribution -- `sarif`/`junit`/`html` render from the same result but read
  none of it, and `--stat` promises a summary-only shape -- rather than
  resolving the manifest and silently dropping the result. A `--write
  FORMAT=PATH` that does carry it makes the combination legal again, since
  the secondary render reuses the same attributed comparison at full report
  mode.

- **Documentation examples that passed a now-config-only setting as a CLI
  operand are fixed, and a gate stops the class recurring.** Demoting a
  hidden flag to a `.abicheck.yml`-only key makes a mechanical rewrite of
  every mention (`--severity-addition error` -> `severity.addition: error`)
  correct in the prose naming the key and wrong in every command line that
  used to pass the flag, where Click reads it as unexpected positional
  operands and exits 64. Seventeen such examples across six pages are now
  config blocks; `scripts/check_docs_contract.py` warns on any
  `abicheck <subcommand> ...` line (or Action `extra-args` value) carrying a
  `key.subkey:` operand, leaving prose and YAML config blocks alone.

### Changed

- **`--policy-file` is folded into `--policy`, which now takes `NAME|PATH`.**
  A built-in profile name (`strict_abi`/`sdk_vendor`/`plugin_abi`) selects
  that profile; anything else is resolved as a policy document — a path, or a
  packaged built-in like `security` — exactly what `--policy-file` did. Two
  flags for one question, the second silently winning when both were given,
  is now one. The Action's `policy-file` input is unchanged and still
  outranks `policy`.

- **`--secondary-format` + `--secondary-output` are folded into
  `--write FORMAT=PATH`** on `compare` and `scan --against`. Half the pair
  was a usage error in either direction, so they were one option spelled as
  two; the format and its destination are now stated together and the two
  half-given checks are gone rather than unreachable.

- **The per-side `--old-ast-frontend`/`--new-ast-frontend` pair is folded
  into a side-aware `--ast-frontend`** on `compare`:
  `--ast-frontend old=castxml --ast-frontend new=clang`, ADR-040 Lever 1's
  same `old=`/`new=` prefix convention as `--header`/`--include`/`--version`.
  A bare value still applies to both sides. `dump`/`scan` keep the
  single-valued spelling.

- **`--include-dependencies` is renamed `--include-system-declarations`.**
  It restores the declarations a system/toolchain header contributed to the
  header AST, which has nothing to do with the `DT_NEEDED` library graph
  `--follow-deps` walks — the old name read as the latter. The snapshot
  field, the `service.run_dump` parameter, and the cache key keep their
  internal `include_dependencies` spelling.

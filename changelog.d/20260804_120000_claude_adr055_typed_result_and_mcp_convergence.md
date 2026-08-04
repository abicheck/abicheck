### Added

- **`CompareResult`, a typed result for the `compare` service verb (ADR-055
  D2).** `diff` (the `DiffResult`), `old_snapshot`, `new_snapshot`, and the
  resolved `suppression` list, so a future field has somewhere to land without
  breaking positional callers. It is what `run_compare_request` and
  `run_compare` return — see the Changed section for that break and its
  one-line migration.
- **`InputSpec.follow_linker_scripts` (ADR-055 D4).** Lets a request decline
  to follow a GNU ld linker script's `INPUT()`/`GROUP()` target to the real
  library. Defaults to `True`, matching the previous behaviour of every
  caller; the MCP server sets it `False`, which is what keeps its
  `MCP_MAX_FILE_SIZE` guard authoritative now that it resolves through the
  shared service layer.

- **`CompareRequest` reaches full parity with `compare`'s own input
  resolution (ADR-055 D1).** Four concepts the CLI could express and a
  Python/MCP caller could not are now request fields: `dwarf_only`,
  `debug_format`, `include_labels` (ADR-050 D1's resolved `path -> label`
  map, carried as a tuple of pairs so the request stays hashable), and
  `--follow-deps` (`follow_dependencies` + `dependency_search_paths` +
  `ld_library_path`). All default to off, so no existing caller changes
  behaviour. `--follow-deps`'s implementation moved from `cli_resolve` into
  the new leaf module `abicheck.dependency_info`, which the CLI and the
  service both depend on.

- **`compare --follow-deps` now works for a GNU ld linker-script input.** A
  distribution's `libfoo.so` is routinely a text linker script naming the real
  `libfoo.so.1`; dependency enrichment gated on the *input's* detected format,
  which is not ELF for a script, so it silently skipped the resolution the
  caller asked for. It now follows the script the same way input resolution
  does — and reads the graph from the resolved ELF rather than the script that
  names it — for any side that hasn't opted out via
  `InputSpec.follow_linker_scripts`.

- **`resolve_compare_request` and `classify_compare_pair` — `run_compare_request`'s
  two phases, now separately callable (ADR-055 D1).** Resolution (validate,
  resolve both sides' evidence and snapshots, `--follow-deps` enrichment,
  depth floor) and classification (suppression/policy, embedded build-source
  diff, `compare_snapshots`, metrics) are individually reusable;
  `run_compare_request` is exactly their composition, so a caller that needs
  no seam is unaffected. `ResolvedComparePair` is the value between them.

### Changed

- **The native `compare` CLI now shares the typed path's input resolution
  (ADR-055 D1's structural half).** `cli_resolve._resolve_compare_snapshots`
  was a second implementation of what `run_compare_request` already did — it
  existed because `compare` must run its Click-dependent ADR-049
  `resolve_and_apply` *between* resolving and classifying, and
  `run_compare_request` was one function with no seam for that. Splitting it
  into the two phases above removed the reason for the copy: the CLI helper
  now builds a `CompareRequest` and delegates. Behaviour is unchanged, down
  to the details that are genuinely CLI-specific — the `click.echo` progress
  notifier, `ValidationError`/`SnapshotError` still surfacing as
  `click.UsageError`/`click.ClickException`, and both sides still resolving
  sequentially.
- **A `--dump-manifest` comparison never resolves both sides at once, on any
  front end.** The CLI resolved sequentially and so was safe; the typed
  `run_compare_request` resolved concurrently and was documented as unable to
  reach a manifest-driven dump at all — which stopped being true when
  `InputSpec.dump_manifest` was added. Since a manifest dump sizes its per-TU
  worker pool from a live `MemAvailable` reading, two starting together sized
  two full pools off the same reading. `resolve_sides_sequentially` now states
  the rule once for every caller.
- **BREAKING (pre-1.0): `run_compare` and `run_compare_request` return a
  `CompareResult`, not a 3-tuple.** With the project not yet holding API
  compatibility, the typed result became the only shape rather than a second
  one carried beside the tuple. A struct can gain a field without
  breaking positional callers, which a tuple cannot. Migrate a positional
  caller in one line: `result, old, new = run_compare(...).as_tuple()`.
- **`CompareRequest.debug_format="auto"` no longer crashes during
  extraction.** `dumper_debug` raises for anything outside
  `dwarf`/`btf`/`ctf` and treats `None` as auto-detect, so the accepted
  `"auto"` had to be translated rather than forwarded — the same translation
  the CLI already does for `--debug-format auto`.
- **A forced ELF debug format is rejected when a side is PE or Mach-O.** Those
  dump paths take no debug-format argument, so the request was silently
  dropped and the run reported success having ignored it; the CLI has always
  rejected the combination up front.

- **The MCP `abi_compare` tool now routes through the Tier-2 chokepoint
  (ADR-055 D4).** It previously resolved its own inputs, loaded its own
  policy/suppression files, and used `compare_snapshots` for the middle
  diffing step only — a second compare engine that could drift from the CLI's
  without anything failing. It now builds one `CompareRequest` and calls
  `run_compare_request`. Output is unchanged: a new parity test asserts
  `abi_compare`'s rendered report and exit code match the CLI `compare`
  command's across policy-profile, policy-file, suppression, `--show-only`,
  `--report-mode`, and severity-aware runs.
- **`abi_compare` rejects an unsupported `language` instead of passing it
  down.** A value outside `c`/`c++` (e.g. `"rust"`) is now a structured
  validation error, matching what the CLI's `--lang` choice has always done.
- **`CompareRequest.debug_format` is validated and case-normalized.** It
  accepts exactly what the CLI's case-insensitive `--debug-format` choice does
  (`auto`/`dwarf`/`btf`/`ctf`); anything else raises `ValidationError` up front
  rather than a bare `ValueError` from inside extraction, and `"DWARF"` now
  behaves for an API caller as it does for the CLI caller who typed it.

### Fixed

- **Four documentation pages quoted stale or duplicated schema versions.**
  `docs/use/output-formats.md` showed `report_schema_version` `"1.0"` and
  still claimed a snapshot's `schema_version` "is currently `8`" — the exact
  bug ADR-055 D3 was written about, alive on a second page after the first
  was fixed — while `docs/reference/check-target.md` quoted `"2.13"` against
  a real `2.26`. Where the version was incidental to the sentence, the page
  now links to the fact owner and holds no copy. Where it must appear
  literally (a JSON output sample, or the owner page's own statement of the
  current value), the `doc-count-sync` AI-readiness check reads the expected
  value from `abicheck.schemas.current()` and fails on drift.

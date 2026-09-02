### Fixed

- **`compare --depth binary` is now accepted (and honoured) against a
  directory/package operand.** It was rejected wholesale alongside
  `--sources`/`--build-info`/`--dump-manifest`, with a message ("the
  per-library fan-out does not collect inline build/source evidence") that
  never actually applied to `binary` — it requests *less* evidence than the
  fan-out already collects by default, so there was nothing about it the
  fan-out couldn't provide. It is now threaded through to every library
  pair, matching a single-pair `compare --depth binary` exactly (clearing
  header/build/source evidence for that pair). `--depth headers` is still
  rejected on this path — the fan-out has no per-library evidence-floor
  enforcement yet — but now with its own message rather than being lumped
  in with `build`/`source`'s "no inline evidence" reasoning, which never
  applied to it either. The composite GitHub Action now forwards an
  explicit `depth: binary` for a directory/package operand instead of
  silently dropping it, and emits a `::notice::` (instead of nothing) when
  it still has to drop `depth: headers`.
- **A directory/package `compare`'s release-level summary
  (`effective_config_fields`/`effective_config_digest`) now reflects the
  real `--policy`/`--policy-file` every library was compared under.** It
  previously computed this block from an empty stand-in carrying only the
  resolved severity config, so `policy.base`/`policy.overrides`/
  `policy.reclassify` all read empty even though the identical policy
  demonstrably was applied to every library's own verdict — indistinguishable
  from a run with no policy override at all. It also now reports the
  policy document's own `base_policy:` (e.g. `sdk_vendor`) instead of the
  CLI's default profile name, which `--policy <path>` always resolves its
  own `policy` parameter back to regardless of the document's real base. It
  also now reports the real `--scope-public-headers`/`--no-scope-public-headers`
  state instead of always reading as the on-by-default value regardless of
  the flag -- found by generalizing the policy-provenance parity test
  across every `effective_config_fields` axis rather than just the one
  reported field.
- **A directory/package `compare` no longer double-prints every warning
  logged through the shared `"abicheck"` logger** (most visibly a policy
  override's "usually causes binary incompatibility" warning, once per
  rule regardless of whether the rule matched anything). The CLI's
  `_setup_verbosity()` helper runs twice in one process for this operand
  shape — once for the outer `compare` command, again when it dispatches
  to the internal per-library release engine — and previously added a
  second `logging.Handler` to the same logger each time, so every later
  log record printed once per accumulated handler.
- **`compare`'s clean-exit (0) verdict resolution now recognises
  `COMPATIBLE_WITH_RISK`.** It previously hard-mapped exit 0 to
  `verdict: COMPATIBLE` unless the report said `BREAKING`/`API_BREAK`, so a
  report the CLI itself classified `COMPATIBLE_WITH_RISK` — a real,
  exit-0 tier the CLI's own exit-code contract already documents — still
  published a plain `COMPATIBLE` output and a step summary reading "No
  binary ABI break detected" via the composite Action, silently dropping
  every risk finding from the Action's own surfaced output even though the
  JSON report carried them in full.
- **A directory/package `compare`'s Markdown report now lists each
  library's individual findings, symbols included.** The `## Libraries`
  table was counts only (`Breaking: 3`); Markdown is the default `format`
  for this operand shape, so identifying which symbol broke needed a
  separate single-pair `compare`, an `--output-dir` re-run, or JSON. Reuses
  the same capped per-library finding list JSON already carried, noting
  when a library's list was truncated -- that note no longer misdirects to
  `--format json`, whose own `findings` field is the identical capped
  projection; only `--output-dir` (or an individual re-run) has the
  complete list.
- **A `binding`-only suppression/reclassify selector now gets a dedicated,
  actionable error** naming the two supported workarounds
  (`symbol_pattern: '.*'` or a real narrowing selector) instead of the
  generic "must have at least one of" list, which never mentioned
  `binding` at all and read as though the field were unrecognized rather
  than deliberately conjunctive-only (a `binding`-only rule would suppress
  every change with that ELF linkage across the whole comparison, with
  nothing else scoping it).
- **The release fan-out's auto `--jobs 0` default now clamps to available
  memory**, mirroring the existing `buildsource/source_replay.py` L4
  worker-sizing pattern through the shared `abicheck.process_resources`
  probe: a bare `os.cpu_count()` default previously sized purely off core
  count, which a very-high-core-count host or a cpu-count-vs-memory-mismatched
  container can push far past available RAM. Tunable via
  `ABICHECK_RELEASE_JOB_MEM_GIB`; an explicit `--jobs N` is never clamped.
- **A stranded (removed-in-new) library's `--bundle-facts-out` entry now
  honours `--depth binary` too.** Every matched pair in the release fan-out
  already had its header inputs cleared under `--depth binary`, but the
  separate resolver used only for a library present in the old side and
  absent from the new one still resolved it with the full header set,
  silently mixing an L2 (header-evidence) snapshot into an otherwise
  binary-only `BundleFacts` output.
- **The composite Action's markdown/text verdict fallback (used whenever no
  JSON report exists) now recognises `COMPATIBLE_WITH_RISK`.** It only
  matched `API_BREAK`/`BREAKING`, so a run with no JSON sidecar (a
  directory/package release compare, or any `--write markdown=...` that
  suppresses it) whose rendered report said `COMPATIBLE_WITH_RISK` still
  published plain `COMPATIBLE`.
- **The composite Action's job summary now has a `COMPATIBLE_WITH_RISK`
  banner.** Its verdict dispatch had no matching arm (and no default), so a
  bash `case` with nothing to match silently omitted the whole verdict line
  from the summary for this tier, even with `add-job-summary: true`.
- **`COMPATIBLE_WITH_RISK` is now declared in `action.yml`'s `verdict`
  output contract, the generated Action reference, and the user guide.**
  It was a real, reachable value the Action could publish, but every
  documented enumeration of `verdict` still listed only the pre-existing
  tiers.
- **The composite Action's directory/package `--depth` handling
  (forwarding `binary`, rejecting `build`/`source`, and the `headers`
  notice) is now case-insensitive,** matching the CLI's own
  `DepthParam.convert()`. `depth: BUILD`/`BINARY`/etc. (any case Action
  YAML happens to use) previously matched none of the bash comparisons:
  `BUILD`/`SOURCE` silently skipped the fail-loud guard entirely (running
  the comparison without the requested evidence instead of refusing to),
  and `BINARY` was silently dropped with no forwarding and no notice.
- **The composite Action can now recover a `COMPATIBLE_WITH_RISK`/
  `BREAKING`/`API_BREAK` verdict from a `format: sarif` primary report even
  when `extra-args` supplies its own non-JSON `--write`.** That combination
  suppresses the Action's automatic JSON sidecar entirely (the CLI's
  `--write` accepts only one format per run), so the verdict reader
  previously fell back to matching the markdown/text `Verdict:` pattern
  against the SARIF document itself, which cannot match SARIF's
  `runs[0].properties.abiVerdict` encoding — publishing plain `COMPATIBLE`
  regardless of the real result. SARIF is itself valid JSON, so the reader
  now falls back to it directly as a true last resort, after every
  higher-fidelity JSON source. `format: html` has the identical trigger and
  remains unaddressed (HTML isn't JSON) — see `docs/contribute/known-gaps.md`.

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
  from a run with no policy override at all.
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
  when a library's list was truncated.
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

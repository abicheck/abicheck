<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **`--explain-patterns` no longer implies `--pattern-verdicts`, and
  `--pattern-verdicts`/`--no-pattern-verdicts` are removed as flags** —
  presentation must never change analysis (ADR-068 D4). Pattern-verdict
  modulation (opaque-pointer/PIMPL demotion, lost-invariant raises) now
  runs unconditionally on every `compare` invocation, gated only by its
  own existing evidence requirement (header-aware-or-better tier for
  demotion) — never by a flag. **Behavior change**: previously,
  `compare OLD NEW --explain-patterns` (with no `--pattern-verdicts`)
  silently turned pattern-verdict modulation *on*, which could change the
  reported verdict and exit code purely because the user asked to see
  *why* a modulation happened — a real correctness bug (a user could get
  `BREAKING` without `--explain-patterns` and `COMPATIBLE_WITH_RISK` with
  it, on the identical operands). `--explain-patterns` is now pure
  rendering: it only controls whether the idiom evidence behind an
  already-decided modulation is printed, never whether the modulation
  happened. A run that relied on `--explain-patterns` alone to enable
  modulation now gets modulation unconditionally (same or richer result,
  never a regression); a run that explicitly passed `--pattern-verdicts`/
  `--no-pattern-verdicts` must drop the flag — the removed spelling exits
  `64` with `No such option`. `--surface-metrics` is unaffected by this
  change and stays a real, independent opt-in flag (its findings are new
  `Change` records merged directly into the result with no separate
  display-only channel to gate, unlike the fixes below, so folding it into
  the same automatic treatment is deferred to a follow-up slice).
- **`--audit-suppressions`'s suppression-rule audit is now always computed**
  when `--suppress` is given, present in JSON/SARIF/JUnit/HTML output
  regardless of the flag; the flag now only controls whether the
  markdown/text/review report additionally prints the human-readable
  "## Suppression Audit" section. Previously, forgetting `--audit-suppressions`
  silently withheld the whole fact (stale/high-risk/expired suppression
  rules) from every output, including machine-readable ones.
- **`--write FORMAT=PATH` is now repeatable** on `compare` — pass it more
  than once (e.g. `--write json=result.json --write markdown=summary.md`)
  to emit several artifacts from one analysis. Each write renders the same
  already-computed result (no re-comparison), full and unfiltered, demangled
  per its own format. `scan --against`'s own `--write` is unchanged
  (still single-value).


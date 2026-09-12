### Changed

- **One export request replaces four report-output mechanisms.** Every native
  command that renders a report (`compare`, `aggregate`, `deps tree`,
  `deps compare`, `project history`, `project plan`, `project validate`) now
  takes a single repeatable `-o FORMAT=DESTINATION`, where `-` is standard
  output and — on `compare`'s directory/package fan-out — a trailing `/` is a
  per-component export. `abicheck compare old.so new.so -o markdown=- -o
  json=report.json` produces both artifacts from one analysis. Every export
  renders the same completed comparison under the same display options, so
  asking for more artifacts never re-runs the analysis, never changes a
  verdict, and never changes an exit code. Collision detection (two exports to
  one destination), stdout exclusivity (at most one `-`), and the `--dry-run`
  rejection now apply across the whole export set rather than to one flag's
  own repeats.

### Removed

- **`--format`, the path-only `-o/--output`, `--write FORMAT=PATH`,
  `--output-dir` and `--max-findings-per-library` are retired**, with no
  aliases — each exits `64` (`No such option`, or a usage error naming the new
  grammar for a path-only `-o`). `--format json` becomes `-o json=-`;
  `--format json -o r.json` and `--write json=r.json` both become
  `-o json=r.json`; `--output-dir reports` becomes `-o json=reports/`, writing
  exactly what it wrote before (one complete report per library plus
  `summary.json`). `--max-findings-per-library` and its
  `ABICHECK_MAX_RELEASE_FINDINGS_PER_LIBRARY` environment variable retire with
  nothing replacing them: a machine export (`json`/`sarif`/`junit`) is now
  never truncated, and a human release summary stays automatically bounded
  with the same `findings_truncated_kinds` disclosure — so the cap is no
  longer a decision anyone has to make.
- One deliberate behaviour change comes with the merge: a `--view show=...`
  display filter now applies to **every** export. The retired `--write`
  rendered its artifact unfiltered regardless of what `--format` was asked
  for, an asymmetry with no principled answer once both artifacts come from
  one operand. Narrowing the display still never hides accounting — every
  machine projection keeps its full disposition/suppression ledger plus
  `show_only_filter`/`filtered_summary`.
- The composite GitHub Action's own inputs (`format`, `output-file`,
  `extra-args`) are unchanged. Internally it now emits one `-o` operand, and
  an `extra-args` export set replaces the Action's own rather than overriding
  one half of it — passing `extra-args: -o json=report.json` means the Action
  injects no export of its own.

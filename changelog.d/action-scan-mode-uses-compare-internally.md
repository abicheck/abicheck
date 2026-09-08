<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Changed

- **The GitHub Action's `mode: scan` now runs `abicheck compare` internally
  for a common subset of invocations** (ADR-068 Phase 4 item 1) — a
  single-artifact `scan --against` run in `format: json` that uses none of
  `new-library-set`/`budget`/`crosscheck`/`risk-rules`/`build-target`.
  Every documented `mode: scan` input keeps working exactly as before;
  this is an internal implementation change with no Action input/output
  change. Invocations using any of the capabilities named above, a
  directory/package `against`, a non-`json` `format`, or scan's one-build
  audit mode (no `against` resolved) still invoke `abicheck scan` directly,
  since `compare` has no equivalent for those yet. Three more cases also
  stay on (or fall back to) the legacy `scan` CLI, closing gaps a
  follow-up review found: an explicitly pinned `depth` (`compare` lacks
  scan's auto-strict pinned-depth evidence contract, so a pin with no
  evidence available used to abort loudly under `scan` but would have
  silently passed under `compare`); a scan-only flag or non-`json`
  `--format` override reaching the same gate only through `extra-args`
  rather than a dedicated Action input; and — the one genuine behavioral
  gap in the migrated `compare` path itself — a cross-source hygiene
  finding (`changes[].cross_source_evolution`) that `scan --against`'s own
  baseline mechanism keeps advisory-only but a real `abicheck compare`
  subprocess does not: detected after the fact from the compare run's own
  JSON report, which is then discarded in favor of re-running through the
  legacy `scan` CLI so the published result matches `scan`'s own semantics.
  A second review round closed three more gaps: the cross-source-finding
  fallback's own report lookup now follows an `extra-args`-only
  `-o`/`--output` override (which silently redirects the real written
  report away from the dedicated `output-file` input, and used to be
  invisible to that lookup); `--max-findings`/`--pattern-verdicts`/
  `--show-suppressed` (three more scan-only options previously left
  reachable through `extra-args`, on the mistaken assumption that a loud
  CLI usage error under `compare` was an acceptable outcome) now also keep
  `mode: scan` on the legacy CLI; and an unpinned/`auto` `depth` combined
  with `since`/`changed-path` (a real diff seed) now also stays on the
  legacy CLI, since `scan`'s risk-driven auto depth resolution and
  `compare`'s own (which infers depth only from `--sources`/`--build-info`,
  never from a diff seed) can resolve to different effective depths for
  the same inputs. A third review round closed five more gaps: `annotate:
  true` is now suppressed for `mode: scan` regardless of which CLI actually
  ran (a migrated compare report carries real `annotations` scan's own
  report shape never did, which would have silently reversed the
  documented "no effect for scan mode" contract); `build-info`/`compile-db`
  given without `sources` and an unpinned depth now also stays on the
  legacy CLI (the identical auto-depth-resolution mismatch as the
  `since`/`changed-path` case, since scan's own auto preset elevates a
  captured build pack all the way to source-target depth, while compare's
  resolver infers only `build` from the same input); the cross-source
  hygiene fallback now also fires on a non-empty `pattern_modulations`
  ledger (ADR-068 D4's pattern-verdict modulation is unconditional on every
  `compare` invocation, but `scan`'s own default is off, so a migrated
  invocation could synthesize a real finding scan's default behavior never
  would have); `-oPATH`-shaped (attached-value) `extra-args` output
  overrides, which this file's tokenizer has never parsed the concatenated
  value of, now force the legacy CLI outright rather than risk the
  cross-source/pattern-verdict fallback silently missing the real report
  location; and `--write text=...` via `extra-args` (valid on `scan`,
  rejected by `compare`, which has no `text` secondary format) now also
  stays on the legacy CLI. One further, deliberately unresolved gap from
  this round: a migrated scan's raw JSON report is compare-shaped
  (`report_schema_version`, top-level `changes`), not scan's own separately
  versioned `scan_schema_version`/`diff`/`coverage`/`crosscheck` contract —
  this Action's own internal logic (job summary, PR comment, exit code)
  already reads either shape identically, but a workflow that parses the
  `report-path` artifact itself expecting scan's shape will see a different
  one. Resolving this fully means the report-schema unification ADR-068's
  own plan already scopes as separate, later work (Phase 5's "one canonical
  report" gap), not something this internal-dispatch change attempts.
  A fourth review round closed three more gaps: an auto-discovered
  `.abicheck.yml`/`.abicheck.yaml` stating an explicit `source: {method:
  auto}` now also stays on the legacy CLI (identical auto-depth-resolution
  mismatch class as `since`/`changed-path` and `build-info`, but via project
  config rather than an Action input — `compare`'s own auto-resolution has
  no equivalent for this value and raises a usage error outright); a
  migrated invocation's `not_comparable` result (exit `16`, `compare`'s own
  code) is now mapped to `scan`'s own `NOT_COMPARABLE` verdict/exit `6`
  rather than falling into the generic `ERROR` branch, which was silently
  changing the published verdict and suppressing the sticky PR comment for
  this valid, reportable outcome; and a bare (unscoped) `--sources`/
  `--build-info`/`--compile-db` reaching a migrated invocation through
  `extra-args` now also stays on the legacy CLI, since an unscoped value
  means "the one candidate" on `scan` but "both operands" on `compare` —
  previously reachable without any error, silently applying the candidate's
  evidence to the baseline side too.

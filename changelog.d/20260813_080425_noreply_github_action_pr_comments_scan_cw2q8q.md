### Fixed

- **The scan sticky comment's compatible-finding accounting was wrong in
  several ways**, all now corrected: a compatible finding promoted to
  blocking by severity policy (e.g. `--severity-addition error`) no longer
  renders in both the Breaking and green "Public API additions" sections at
  once, and the exact promoted/addition totals now come from the severity
  gate's own per-category counts rather than a possibly report-capped
  finding list; the safe-finding count is derived from the diff's full
  `compatible` scalar (with a matching `diff.quality` itemization) rather
  than only the addition-shaped subset, so a scan whose only findings are
  compatible-but-non-addition (a quality-category change, or a
  policy-demoted removal) no longer reports zero changes and gets its
  sticky comment silently skipped.
- **Cross-check findings** (a separate evidence axis from the baseline diff)
  now surface in the comment for both an audit-only run and a baseline
  comparison, instead of silently rendering (or deleting) a green "no
  changes" comment next to a red `fail-on-api-break` check; an un-promoted
  cross-check on a baseline comparison stays advisory-only, matching
  `scan`'s own exit-code contract, and the summary count reflects the exact
  summed occurrence count per check rather than one row per distinct kind.
  A promoted RISK-tier cross-check now renders a "Compatibility risk blocks
  this PR" headline instead of misreporting a real API break.
- **A crafted scanned-artifact filename could inject Markdown into the
  sticky comment**: the comment header now escapes the subject (an
  untrusted filename passed through the Action's `--subject`) before
  rendering it, closing an injection vector via a backtick/newline in the
  artifact name.
- **The Action's PR-comment renderer now uses the resolved Python
  interpreter** (`$_PY_BIN`, already used by every other Python invocation
  in `run.sh`) instead of a hard-coded `python3`, which could silently fail
  on a Windows Git Bash runner where only `python`/`python.exe` is on PATH.
- **The temporary JSON report the Action writes for the sticky comment is
  now removed by the script's cleanup trap**, alongside the other temp
  files it already cleaned up — previously leaked on every non-JSON,
  single-artifact `compare`/`scan` run, accumulating indefinitely on a
  persistent self-hosted runner.
- **A `scan --against` evidence-quality finding** (e.g.
  `source_fact_coverage_incomplete`, `layer_coverage_asymmetric`,
  `dwarf_info_missing`) now renders in the comment's "Analysis incomplete"
  section, matching `compare`'s own report, instead of a misleading
  "Compatibility risk blocks this PR" headline (or, for the
  COMPATIBLE-severity `dwarf_info_missing`, a false-green "No compatibility
  impact detected") for what is really a missing-evidence signal rather
  than a detected API/ABI change. The exact header totals now exclude
  these occurrences too — including ones cut by the report cap and
  recoverable only from the truncation ledger — so the count never
  disagrees with what's itemized under Review/Safe. A run whose only
  gating cause was a promoted or `--gate-api-break`'d evidence-quality
  finding now keeps the sticky comment's blocking "🛑 Source analysis
  incomplete" headline instead of understating it as the advisory "⚠️
  Analysis coverage reduced" — including a compatible-severity evidence
  kind (`dwarf_info_missing`) promoted by `quality_issues: error`, which
  previously double-counted into both the Breaking total and Incomplete
  section at once.
- **`scan --secondary-format text` next to a `--format json` primary run**
  no longer silently loses the ADR-049 §7 coverage-gate explanation: the
  stderr notice explaining a coverage-floored exit code (e.g. "Exit code
  floored to 1") now fires whenever *either* renderer in play is `text` —
  previously it only checked the primary format, so the secondary text
  report (which, unlike JSON, never carries the coverage ledger itself) had
  no explanation reachable anywhere for why the run exited non-zero.
- **The sticky comment's exact "N analysis incomplete" count** now accounts
  for evidence-quality findings cut by the report cap, the same way the
  Breaking/Review/Safe totals already do: `diff["findings_truncated_kinds"]`
  can record a cut occurrence of e.g. `source_fact_coverage_incomplete` that
  never made it into the itemized list, and the header count previously
  read only `len(model.incomplete)` — under-reporting (e.g. "20 analysis
  incomplete" for 25 real occurrences) right next to a truncation note
  claiming the counts above were exact.
- **`fail-on-api-break` no longer promotes an api_break finding to Breaking
  under a severity-aware `scan --against` run that left `potential_breaking`
  at its non-error default** (`warning`/`info`): the sticky comment
  previously rendered "Source API break blocks this PR" — including for a
  promoted evidence-quality finding — next to an actually-green Action
  check, because `action/run.sh`'s own ADVISORY_BREAK logic keeps the real
  exit at 0 in exactly this scheme/config combination regardless of
  `fail-on-api-break`. The fixed api_break → breaking mapping now applies
  only under the legacy exit-code scheme, where it really is unconditional;
  the severity-aware case already renders correctly from the resolved
  `potential_breaking` category level alone.
- **A fully report-cap-truncated analysis-incomplete bucket now still
  renders**: when earlier buckets (e.g. 20 breaking findings) consumed the
  entire shared findings cap, every analysis-incomplete occurrence could be
  cut before any of it was itemized — `model.incomplete_total` stayed exact
  and positive, but `model.incomplete` itself was empty, and every
  render-time check (headline, header count, note, section) was keyed on
  `bool(model.incomplete)` alone, so the comment silently omitted the whole
  bucket right next to a truncation note claiming the counts above were
  exact. `CommentModel.has_incomplete` now covers both cases, and the
  section itself renders a placeholder row naming the exact cut count when
  nothing survived itemization, instead of vanishing outright. This applies
  to `compare`'s and `release`'s own analysis-incomplete bucket too (shared
  rendering code), though neither mode's own list is currently cap-truncated
  in a way that reaches the empty-but-nonzero case.
- **A real ABI break no longer mislabels as a policy-only block when a
  small `--max-findings` cap reserves the shared findings-list budget for a
  severity-promoted compatible addition/quality finding instead**:
  `cli_scan_baseline._add_severity_blocking_compatible_findings`'s reserved
  floor only guarantees a *minimum* itemized representation for a promoted
  compatible category, not completeness, so e.g. `--max-findings 1` could
  push a genuine ABI break out of the itemized list entirely even with
  `diff["breaking"] == 1` and a `BREAKING` verdict. `breaking_categories`/
  `breaking_severities` — previously derived only from that possibly-capped
  itemized list — now also draw from the same exact per-category scalars
  already used elsewhere for the header's exact totals, so the headline
  ("ABI BREAKING") and gate note no longer contradict the real verdict (and
  no longer render a false "Compatibility: COMPATIBLE" claim next to it).
- **A breaking-bucket evidence-quality finding's blocking determination now
  honors the resolved `abi_breaking` severity level, not `fail-on-breaking`
  alone**: unlike `compare`, `scan`'s own `action/run.sh` gate has an extra
  unconditional block for a genuinely severity-configured-`error` category
  (`BREAKING`/`API_BREAK` tiers fail the step regardless of
  `fail-on-breaking`/`fail-on-api-break` once that's what produced the
  exit) — so under the severity-aware scheme, an `abi_breaking: warning`
  finding must not read as blocking just because `fail-on-breaking`
  defaults to `true`, and an `abi_breaking: error` finding must still read
  as blocking even with `fail-on-breaking: false`. The `potential_breaking`
  branch already had this right; the `abi_breaking` branch previously
  didn't check the resolved level at all.
- **A promoted `--crosscheck` finding no longer renders as Breaking on a
  `NOT_COMPARABLE` run**: `scan_engine.run_scan_core` deliberately skips
  cross-check severity folding entirely when a scope/profile mismatch means
  no baseline comparison ran (exit unconditionally `6`), so a promoted
  cross-check never actually gated that run's exit code —
  `fail-on-api-break` no longer moves it into Breaking next to the real,
  unconditional `NOT_COMPARABLE` block, which previously produced a "Source
  API break blocks this PR" headline for what is really a scope mismatch.
- **A `--contract-evaluation` addition/quality finding left `NOT_EVALUATED`
  (proven outside the declared contract, or unresolved for want of
  evidence) is no longer counted as promoted by a `severity-addition:
  error`/`severity-quality-issues: error` config**: the severity JSON's
  per-category `count` is a *display* count (classified purely by kind),
  while the real gate (`compute_gate_decision`) correctly excludes
  `NOT_EVALUATED` findings — reading the display count fabricated a
  nonzero Breaking total and a "blocked by policy" headline for a run
  whose real exit was clean. Corrected (after a first attempt that read
  from a list where the target rows can never actually appear) by deriving
  the promoted count from `diff["additions"]`/`diff["quality"]` instead —
  those are already gate-eligible-only by construction, since they itemize
  `diff.compatible`, which itself excludes every `NOT_EVALUATED` finding
  before the addition/quality split ever runs.
- **The scan header's exact breaking/review totals no longer go negative**
  under a narrow, inconsistent-report edge case: the truncated portion of
  an evidence-quality finding count is attributed to that kind's
  registry-default bucket, which can (under an unusual maintainer policy
  override, combined with truncation) exceed the raw scalar it's being
  subtracted from. Floored at 0, matching the guard `scan_safe_total`
  already had for the identical class of subtraction.
- **A crafted scanned-artifact filename containing a lone carriage return
  (`\r`, not just `\n`) could still inject Markdown into the sticky
  comment's header**: `_esc` now neutralizes `\r` too — CommonMark treats a
  bare carriage return as a line ending exactly like `\n`, which the
  original backtick/`\n` injection fix didn't cover.
- **`docs/reference/github-action-inputs.md`'s generated `verdict` output
  description now wraps `--severity-*`/`--exit-code-scheme` in code spans**
  (fixed at the `action.yml` source and regenerated), clearing a
  markdownlint MD037 warning.

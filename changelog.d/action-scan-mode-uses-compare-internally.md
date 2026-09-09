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
  A fifth review round closed three more gaps: the `source.method` config
  check is widened from `method: auto` alone to ANY explicit value (a
  non-`auto` pinned method diverges too — `scan` risk-scores past it
  regardless, `compare` genuinely honors it — a different failure mode, same
  root cause); that same check now uses `[[:space:]]` instead of `\s` (a GNU
  grep extension BSD/macOS's stock grep doesn't recognize, silently missing
  the check entirely on those platforms); and the cross-source/pattern-
  verdict fallback now also fires when the migrated `compare` run's
  effective JSON destination is itself unreadable (`output-file: /dev/null`,
  or the equivalent via `extra-args`) — previously indistinguishable from a
  genuine dry run's legitimately-absent report, so an unverifiable real
  result was silently trusted as-is; now falls back to the legacy CLI for
  any real (non-dry-run) invocation whose report can't be read back,
  correctly leaving a genuine dry run's own single-invocation behavior
  unaffected.
  A sixth review round closed two more gaps: a `python_stable_abi_violation`
  finding (`--abi3`'s stable-ABI audit) is now included in the
  cross-source/pattern-verdict fallback's own report check -- `compare`
  policy-scores this finding directly in its `changes` list, but `scan`
  keeps it in its own advisory crosscheck result unless explicitly promoted
  via `--crosscheck python_stable_abi_violation=error`, so a policy override
  reclassifying it could silently score `BREAKING` under a migrated run for
  operands `scan` itself would have exited 0/`COMPATIBLE` for; and a JSON
  `--against`/`abi-baseline` snapshot explicitly tagged `dependency_scope:
  "full"` (dumped with `--include-system-declarations`) now also stays on
  the legacy CLI -- `scan` peeks this tag and extracts a live candidate
  unfiltered to match, but `compare` has no automatic equivalent, so the
  comparability gate's own dependency-scope check used to reject the pair
  outright as `NOT_COMPARABLE` where `scan` succeeds. A CodeRabbit pass over
  the same round widened the `.abicheck.yml`/`.abicheck.yaml` `source.method`
  detection to also match YAML's flow-style spelling (`source: {method:
  auto}`, on one line), which the original block-style-only pattern missed
  entirely -- silently migrating that configuration to `compare` and hitting
  the exact usage error the check exists to prevent. A second Codex pass on
  the same round found the check still only ever inspected
  `$PWD/.abicheck.yml`/`.abicheck.yaml` -- an explicit `build-config`/
  `--config FILE` selects a TRUSTED project config (cwd auto-discovery
  applies only when it's omitted, per `scan --help-all`), so a
  `source.method` living in a `build-config`-named file elsewhere went
  undetected. The check now inspects the effective config (`build-config`
  when given, else cwd auto-discovery) rather than always the latter. A
  fresh Codex finding on the same round closed one more gap: a repeated
  `--write` reaching a migrated invocation through `extra-args` now also
  stays on the legacy CLI -- `scan --write` is singular (a repeat just
  keeps the last value), but `compare --write` is repeatable and rejects
  two occurrences naming the same destination as a real usage error, so a
  previously valid `--write json=report.json --write json=report.json`
  scan step would otherwise start hard-failing under a migrated
  invocation.
  A seventh review round closed two more gaps: an effective `--config`
  reaching a migrated invocation through `extra-args` (rather than the
  dedicated `build-config` input) now also stays on the legacy CLI, since
  Click's own last-flag-wins means it always overrides `build-config` too,
  and `_config_sets_source_method` never inspected it; and the migrated
  `compare` invocation now reproduces `cli_scan_baseline`'s own old-side
  header-reuse fallback for a native `--against` library -- when no
  dedicated `old-header` is given but the candidate has header evidence of
  its own (`new-header`/`public-header-dir`), `scan` deliberately re-parses
  the old side through the SAME headers as the candidate rather than
  leaving it a headerless binary next to a header-evidenced new side, and
  the un-fixed migrated `compare` invocation left the old side with no
  header at all in exactly this shape, changing findings.
  An eighth review round closed three more gaps: the native-baseline
  header-reuse fallback above now also reuses the candidate's own
  `new-include` for the old side (unless an explicit `old-include` was
  given) -- the reused header can itself depend on an include path only
  given via `new-include`, and without this the migrated invocation could
  fail parsing the old header entirely where `scan` succeeded; the
  `.abicheck.yml`/`.abicheck.yaml` `source.method` detection now also
  matches a quoted YAML key (`source: {"method": auto}`, or an indented
  `"method": auto`), which valid YAML permits and the project-config
  loader accepts, but the original pattern required a bare `method`; and
  an effective `dry-run: true` (or the `estimate` alias, or `--dry-run` via
  `extra-args`) now also stays on the legacy CLI -- scan's own dry-run
  preview (`action.yml`'s documented "scan preview" contract) reports the
  PR preset's risk-resolved collect mode and scan-specific per-layer
  candidate cost, which `compare --dry-run`'s own preview does not
  reproduce.
  A ninth review round closed two more gaps, both rooted in the same
  discovery-precedence miss: `scan`'s own effective-config resolution has
  three tiers (an explicit `build-config`, else `discover_build_config` at
  `--sources`' own tree root, else an upward walk from the checkout root),
  but this migration previously only ever covered the first and third,
  entirely missing the middle one -- so a `source.method`/`python.
  abi3_floor`/severity/scope/suppression/gate setting living in a config
  discovered from `--sources`' own tree went unseen. Fixed generally: a new
  `_resolve_scan_effective_config_path` helper reproduces all three tiers
  via the real python discovery functions, used both to widen the existing
  `source.method` detection and to explicitly forward `--config` to the
  migrated `compare` invocation whenever `--sources` has its own config
  compare's default resolution wouldn't otherwise see. A new, dedicated
  `python.abi3_floor` config-key detection (the same discovery precedence)
  closes the other reported gap: `scan` enables its stable-ABI audit only
  from an explicit `--abi3` CLI value, never from project config, but
  `compare` also enables it from this key -- a migrated invocation with no
  `--abi3` given at all could still run the audit under `compare` and fail
  its own precondition (a non-CPython-extension pair) with exit 7, where
  `scan` would simply never have looked at that key. Also fixed a
  self-inflicted bug in the new resolver's own first draft: its inline
  python subprocess runs from an isolated temp directory (this file's
  established untrusted-checkout-avoidance pattern), so the discovery
  function's own `Path.cwd()` default silently resolved against THAT
  directory instead of the real checkout root -- caught by this round's
  own regression tests regressing the pre-existing cwd-discovery case,
  fixed by threading the real working directory through explicitly.
  Separately, a CI-only failure that reproduced deterministically across
  every unit-test lane (`['compare', 'scan', 'scan']` instead of
  `['compare', 'scan']`) was traced to a genuine, PRE-EXISTING interaction
  between two independent reuse-or-rerun mechanisms rather than a new
  regression: `_maybe_post_pr_comment`'s own JSON-acquisition rerun
  (gated on the real ambient `GITHUB_EVENT_NAME=pull_request` this test
  suite's own CI job runs under, never set in a local/sandboxed run) also
  needs a readable report and, given the same unreadable `/dev/null`
  destination the cross-source fallback tests already use, independently
  reruns a third time. The published verdict/exit-code were never affected
  (both come from the second invocation either way); fixed by scoping the
  two affected tests to `INPUT_PR_COMMENT: false`, isolating them from a
  mechanism outside what they actually test. A diagnostic `argv_log.txt`
  (timestamp/pid/ppid/full argv per stub invocation, harmless and left in
  place) is what surfaced the real third command line and made this
  traceable at all.
  A tenth review round closed one more gap in the native-baseline
  header-reuse fallback: a sided `new=` header/include value reaching the
  migrated invocation only through `extra-args: -H new=PATH`/`--header
  new=PATH` (or `-I new=PATH`/`--include new=PATH`) was invisible to the
  reuse check, since `extra-args` is appended to the real command line only
  at the very end of this script -- well after the fallback already decided
  whether OLD needs a reused header at all. `scan`'s own
  `_resolve_baseline_header_scope` folds `extra-args`' `-H`/`--header`
  values into the SAME candidate `headers` list the dedicated `new-header`/
  `public-header-dir` inputs populate, with no distinction between the two
  sources, so the migrated `compare` invocation left OLD headerless in
  exactly this shape too. Fixed by two new helpers
  (`_extra_args_new_side_header_values`/`_extra_args_new_side_include_values`)
  that extract any sided `new=`-scoped `-H`/`--header`/`-I`/`--include`
  value from `extra-args` and fold it into the same reuse fallback the
  dedicated inputs already trigger. A BARE (unsided) `-H`/`--header` value
  in `extra-args` deliberately needs no such help -- `compare` already
  applies an unsided value to BOTH sides on its own (ADR-040's own
  base/old/new fan-out), so OLD is never left headerless in that shape to
  begin with.
  An eleventh review round closed two more gaps in the same fallback:
  `-H`/`--header` (and `-I`/`--include`) are repeatable, so
  `extra-args: -H old=old.h -H new=new.h` already gave OLD its own real
  header, but the fallback had no way to see it and injected an ADDITIONAL
  `-H old=new.h` from the candidate's own `new=` value alongside it --
  Click keeps every occurrence, so OLD ended up parsing through both
  headers at once. Fixed by a new `_extra_args_has_old_side_value` check,
  gating the reuse fallback for both `-H`/`--header` and `-I`/`--include`.
  Separately, a valid Click attached-value short form (`-Hnew=api.h`, no
  space) reaches this file's shared tokenizer as one opaque token --
  documented as a pre-existing, accepted limit of every extra-args scan in
  this file -- so it was invisible to the tenth round's own `new=`
  detection, silently leaving OLD headerless in exactly this spelling.
  Fixed the same way the pre-existing `-oPATH` case already handles this:
  `-H?*`/`-I?*` now force the legacy CLI outright rather than risk
  guessing, in `_extra_args_forces_legacy_scan_cli`.
  A twelfth review round closed two more gaps: a project config's `debug:`
  namespace (`dwarf_only`/`format`/`debuginfod`/`debuginfod_url`) is
  resolved and applied by `compare`'s operand extraction, but `scan`'s own
  baseline resolver never reads any of these four keys at all -- verified
  directly: a stripped ELF pair with headers and `debug: {dwarf_only:
  true}`, `scan` reports `COMPATIBLE` reading the header AST (never even
  seeing the DWARF-only request), while the migrated `compare` invocation
  honors the config and reports `COMPATIBLE_WITH_RISK` from a completely
  different evidence source -- with real DWARF available, `compare`
  instead honors `dwarf_only` and ignores the supplied headers entirely,
  the opposite direction of divergence. Fixed with a new
  `_config_sets_debug_options` check, the same narrow textual match and
  effective-config resolution as `_config_sets_abi3_floor`. Separately, the
  native-baseline include-reuse fallback was nested inside the header-reuse
  `if` (gated on `old-header` being absent), but
  `cli_scan_baseline._resolve_baseline_header_scope`'s own `bl_includes =
  baseline_includes or includes` fallback fires whenever `old-include` is
  absent REGARDLESS of whether `old-header` was given -- only the *header*
  half of that function's return value depends on `old-header` being
  empty. Verified directly: `old-header` given, `new-include` given, no
  `old-include` -- legacy `scan` exits 0, the un-fixed migrated invocation
  exited 1 on a "types.h not found" error because the reused header's own
  include dependency never reached OLD. Fixed by making the include-reuse
  fallback its own condition, independent of whether the header-reuse
  fallback fired.
  A thirteenth review round closed two more gaps in the same fallback: a
  BARE (unsided) header/include -- the dedicated `header`/`include` inputs,
  or a bare `extra-args -H`/`-I` value -- already reaches OLD via the
  ordinary bare forwarding this file already does (`cli_scan.py`'s own
  ADR-040 split folds a bare value into BOTH `headers` and `baseline_
  header`/`baseline_include` at once), so `_resolve_baseline_header_scope`
  never reuses anything in that shape at all; without accounting for it,
  the reuse fallback injected the candidate-only `new-header`/`new-include`
  on top of the already-shared bare value, parsing OLD through evidence
  that belongs only to NEW. Fixed with a new `_extra_args_has_bare_value`
  check (mirroring `_extra_args_has_old_side_value`'s shape for the bare
  case), gating both the header- and include-reuse fallbacks. Separately, a
  Click-valid verbose-clustered attached spelling (`-vHnew=api.h`) reaches
  this file's tokenizer as one opaque token whose name doesn't even start
  with `-H`/`-I` (it starts with `-v`) -- `_extra_args_expand_short_
  clusters` deliberately leaves any cluster with something attached after
  its value char unexpanded, so the previous round's own `-H?*`/`-I?*`
  patterns never matched it. Fixed by widening those patterns to `-v*H?*`/
  `-v*I?*` in `_extra_args_forces_legacy_scan_cli`.
  A fourteenth review round found a deeper native-baseline reuse gap this
  migration cannot safely close by forwarding literal flags: `scan_engine.
  py`'s own reuse always feeds the candidate's OWN *resolved*
  `effective_includes` (seeded from a `--sources`/`--build-info`/compile-
  database match, not just the literal `new-include` value) into the
  baseline's native parse, and further reuses the candidate's own folded
  compile context when the baseline reuses its header/include scope.
  Verified directly: a header depending on `types.h`, found only through
  the compile database's own include path (never a literal `new-include`)
  -- `scan` exits 0, the migrated invocation exited 1 with OLD unable to
  find `types.h`. Reproducing this would mean re-running the same compile-
  database matching logic in bash, which this migration does not attempt;
  fixed with a new `_migrated_compare_against_native_baseline_has_build_
  evidence` gate condition -- a native `--against` library combined with
  any of `--sources`/`--build-info`/`--compile-db` now stays on the legacy
  CLI outright.
  A fifteenth review round raised a genuine `scan`/`compare` provenance
  asymmetry (a lone `-H`/`--header` *file* with no directory leaves every
  declaration `UNKNOWN` on `scan`, per `workflows.scan_config.
  public_provenance_set`'s own docstring, but still opts `compare` into
  classification via `provenance.build_public_set`'s `have_public_set =
  bool(headers or dirs)`) that could move a change into `out_of_surface`
  before verdict computation, invisible to this file's own cross-source-
  hygiene fallback. Investigated and NOT fixed here: tracing the code
  confirmed this predates this migration entirely and is already
  documented as a deliberate, accepted `compare`-specific behavior in
  `workflows/cross_source_evolution.py`'s own module docstring ("that PR
  left that `compare`-specific behavior exactly as it found it rather than
  retroactively tightening it") -- any direct `abicheck compare` invocation
  with a file-only header already has this behavior, independent of this
  Action. A first attempt at forcing the legacy CLI whenever header
  evidence was file-only with no directory had to be reverted: it disabled
  the migration's own primary, documented use case (the file-only
  `new-header` reproduction the seventh review round itself is built on)
  and broke the majority of the native-baseline header-reuse test suite.
  A correct fix would mean either reproducing `out_of_surface`'s full
  computation in bash, or retroactively tightening `compare`'s own
  provenance rule to match `scan`'s -- a real product change to `compare`
  itself, previously and deliberately declined, out of scope for an
  Action-migration PR.

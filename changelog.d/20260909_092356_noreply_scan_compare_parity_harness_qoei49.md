### Fixed

- **A `RESOLVED` cross-source finding no longer fails a passing `compare`/
  `scan --against` run.** `compare()`'s automatic cross-source-checks stage
  (ADR-068 D3) stamps a finding present on OLD but absent on NEW as
  `RESOLVED` — the problem was fixed. That finding used to reach the
  verdict/exit-code gate exactly like a currently-present one, so *fixing*
  a pre-existing issue like `private_header_leak` could still report
  `API_BREAK`/exit 2, contradicting the migration plan's own acceptance
  criterion ("resolved, and visible on a passing run"). A `RESOLVED`
  finding now stays fully visible in the report and every disposition
  ledger, but no longer contributes to the verdict, the severity-preset
  exit code, or its own per-finding `gate_contribution` field. This
  exclusion applies at every verdict-computing chokepoint: `scan
  --against`'s own `--crosscheck KEY=off` post-removal verdict recompute,
  `compare`'s own opt-in `--surface-metrics`/`--pattern-verdicts`
  verdict recomputations, and `recommend_release`'s conserved-delta
  reading of a suppressed finding's kind-level class — each previously
  could resurrect an already-`RESOLVED` cross-source finding (or, for the
  `--crosscheck KEY=off` recompute, an already-out-of-contract one too)
  into a failing verdict or a waived-major-break release recommendation
  when an unrelated check was disabled, an unrelated public-surface/pattern
  finding also fired, or an ordinary suppression rule happened to match it,
  on the same run.
- **`mode: scan` Action requests carrying a `compare`-only flag through
  `extra-args` (`--surface-metrics`, `--used-by`, `--diagnostic-comparison`,
  and about three dozen others `scan --help-all` doesn't accept) now stay
  on the legacy `scan` CLI instead of silently routing to `compare`.**
  Previously such a request either reproduced a `compare`-only usage error
  a `mode: scan` caller should never see, or — for the consumer-scoping and
  surface-metrics flags specifically — silently *succeeded* against a
  different finding set/gate scope than the caller's `scan`-shaped workflow
  was written against.
- **A `mode: scan` step's `severity-preset` input now applies consistently
  whichever internal route the Action picks for it.** The legacy `scan` CLI
  branch (selected for, e.g., a stored-JSON baseline) never forwarded
  `severity-preset` at all, while the translated `compare` branches
  (selected for a live baseline) already did — so the identical `mode:
  scan` request with `severity-preset: strict`/`info-only` set could change
  gate labels and exit code solely because of which route the baseline
  shape happened to select, not because of anything the step itself asked
  for differently.
- **`scan --against --crosscheck KEY=info`/`=warning` now actually demotes
  the automatic cross-source-checks stage's own finding, matching the
  single-snapshot `scan crosscheck` mechanism's existing "info/warning
  never gate" contract.** Previously the demoted level kept the finding
  visible (as documented) but had no effect on its contribution to the
  baseline scan's verdict or exit code — it still scored at its
  `ChangeKind`'s own default severity under both the legacy verdict scheme
  and `--severity-preset`/`.abicheck.yml`'s `severity:` scheme, so an
  explicit `=info`/`=warning` override could still fail a run the same
  flag on the sibling single-snapshot mechanism would pass. A first fix
  attempt introduced two further regressions, also fixed here: the
  demotion was folded into the *technical verdict* itself
  (`--crosscheck exported_not_public=warning --severity-preset strict`
  reported root `NO_CHANGE` while still listing the finding — an
  info/warning override is a gating-only knob and must never launder the
  real, observed compatibility classification), and the persisted
  `diff.exit` block could disagree with the actual exit code/
  `diff.severity.exit_code` for the identical run. A second fix attempt
  also made the demoted finding's own `severity.categories.*.count` (a
  separate, display-only field from the exit code) silently drop to one
  less than `diff.findings` actually lists — that count now includes the
  demoted finding too, matching this fix's own "stays fully visible"
  contract.
- **A one-sided `scan`/`scan --artifact-set` audit request with an explicit
  `--crosscheck KEY=warning`/`=info` override no longer raises a usage
  error (exit 64).** A first attempt at the fix above mistakenly treated
  the underlying `severities` field as meaningful only for a baseline
  `--against` comparison, when it is also the audit-only, single-snapshot
  crosscheck mechanism's own input — exactly the documented, pre-existing
  shape a plain `scan --crosscheck KEY=warning` (no baseline at all)
  builds.
- **A `mode: scan` Action request with a live baseline (native `.so`, an
  explicit `headers`/`binary` depth, no other still-legacy-only capability)
  now stays on the legacy `scan` CLI unless the workflow explicitly opts
  into `extra-args: --pattern-verdicts`, instead of always routing to
  `compare`.** `scan --against` defaults pattern-verdict modulation off;
  `compare`'s own modulation has been unconditional since ADR-068 D4, with
  no flag left on that side to turn it off at all. Routing a default
  baseline scan onto `compare` therefore silently applied an
  idiom/anti-pattern-evidence-gated modulation axis — able to demote an
  opaque-pointer/PIMPL layout change or raise a break for a lost opacity
  guarantee — that a `mode: scan` caller's workflow was never written
  against, purely because the Action picked a different internal CLI. An
  explicit bare `--pattern-verdicts` (matching `compare`'s forced-on
  behavior exactly) is the one case that is safe to route. A first fix
  attempt only fixed the routing *decision* — the flag itself still reached
  the translated `compare` invocation unstripped, on the one request shape
  the fix was meant to let through, failing it with a real "no such option"
  usage error (`compare` has no `--pattern-verdicts` flag at all). It is
  now stripped before the translated command runs.
- **A `mode: scan` Action step with a `severity-preset`-driven error (e.g.
  `severity-preset: strict` on a risk/API finding) now unconditionally
  fails when the request was routed through `compare`'s translation, the
  same way it always has on the legacy `scan` CLI.** The Action's own
  wrapper-level pass/fail dispatch (separate from the underlying CLI's own
  exit code, which already correctly reflected the severity error) was
  keyed on which CLI actually ran internally rather than on the caller's
  own `mode: scan` request — so a translated request fell into `compare`'s
  dispatch, which lets `fail-on-api-break` (default `false`) swallow a
  severity policy the caller explicitly configured as an error, a false
  green a `mode: scan` workflow never expected.
- **A `RESOLVED` cross-source finding no longer keeps counting against a
  consumer-scoped verdict (`--used-by`'s app-compatibility check and the
  plugin-host `--required-symbol(s)` contract).** `appcompat.py`'s
  `breaking_for_app`/`breaking_for_host` list is both the app/host verdict's
  scoring input *and* this module's own display/audit list — so an
  already-fixed cross-source finding correctly stayed listed (a `RESOLVED`
  finding stays visible everywhere per the first bullet above) but also kept
  scoring `_compute_appcompat_verdict`'s `COMPATIBLE_WITH_RISK`/breaking
  verdict, the identical class of bug the checker's own
  `_verdict_scored_population` and `scan --against`'s
  `verdict_scored_changes` already close at their own chokepoints. The
  verdict computation now excludes a `RESOLVED` finding while leaving the
  returned list itself — and every other reader of it (the report, the
  uncovered-symbol scan, the disposition ledger) — unfiltered.
- **A `mode: scan` Action request routed through `compare`'s translation
  no longer silently discards a `build-config` input's own settings when a
  cross-compilation input (`gcc-path`/`gcc-prefix`/`gcc-options`/`sysroot`/
  `nostdinc`/`ast-frontend`) is also given.** `add_compile_context_flags`
  already merges an explicit `build-config` into its own synthesized
  `compile:` overlay and appends the merged result as `--config`, but the
  scan→compare translation branch's own unconditional
  `add_single_flag "--config" "$INPUT_BUILD_CONFIG"` right after it
  appended a *second*, raw, unmerged `--config` — Click keeps only the
  last occurrence on the command line, so the operator's own `release:`/
  other `build-config` settings silently won over the synthesized
  compiler/sysroot/macros overlay instead of being merged with it, the
  opposite of what every other CLI-flag-consolidation branch that calls
  `add_compile_context_flags` already guards against. Guarded with the
  same `_cmd_has_config_flag` check the native `dump`/`compare` branches'
  identical `--config` append already carries.

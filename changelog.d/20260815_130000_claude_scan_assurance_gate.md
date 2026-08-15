<!-- P0.4 analysis-assurance parity for `scan --against`, plus the composite
     Action's own gap for both commands. Found by a fresh main->lab
     end-to-end audit. -->

### Fixed

- **`scan --against` now supports `--require-complete-analysis` (P0.4).**
  `compare` has gated on `analysis_assurance.status` since P0.4 landed;
  `scan --against` computed the identical `analysis_assurance` result on
  every run (`checker.compare` always attaches it) but never accepted the
  flag, never folded its exit-code floor, and never emitted the block in
  its own JSON summary at all — a run whose evidence was genuinely
  incomplete gated on `compare` and passed on `scan` for the identical
  pair. `scan --against --require-complete-analysis` now floors the exit
  code to 1 the same `max`-folded way `compare`'s own flag does (never
  lowering a real 2/4/5/6), and `scan --format json`'s summary always
  carries `analysis_assurance` under `diff`, regardless of the flag —
  mirroring `compare`'s report shape at the root. Rejected as a usage
  error without `--against`, alongside the rest of the baseline-only
  flags (`--policy`, `--severity-preset`, ...).

- **The composite Action now recognizes P0.4's analysis-assurance exit
  and gates on it unconditionally, via a new dedicated
  `require-complete-analysis` input.** `--require-complete-analysis` (on
  either `compare` or `scan --against`) floors the CLI exit code to 1 on
  incomplete evidence, but `action/run.sh`'s exit-1 disambiguation had no
  notion of this axis: it fell through to `SEVERITY_ERROR` on `compare` (a
  severity-policy failure it is not) or the catch-all `ERROR` on `scan`
  (an operational failure it is not), which meant a genuinely
  assurance-gated run could read as something else in the job summary —
  and, on `scan`, was never actually documented as failing the step for
  the right reason. `run.sh` now maps this to a new `ANALYSIS_INCOMPLETE`
  verdict (mirroring ADR-049's own `COVERAGE_INCOMPLETE` orthogonal-axis
  treatment: never rewrites the compatibility verdict, unconditional — no
  `fail-on-*` flag disables it), on both commands, including when it
  coincides with a severity or contract-coverage gate on the same exit 1.
  Gating on this axis is a plain boolean read of the new
  `require-complete-analysis` input — not a scan of `extra-args` or the
  constructed CLI invocation — closing out three rounds of Codex-found
  false positives from inferring the flag's presence from other signals
  (an unanchored stderr grep; a `$CMD`-array token scan colliding with
  another input's own value; an `extra-args`-token scan colliding with an
  adjacent option's own value) with a design that structurally cannot
  reproduce any of them. Two more Codex findings on the new input itself:
  the "Run abicheck" step's own `env:` block never mapped the declared
  `require-complete-analysis` input to `INPUT_REQUIRE_COMPLETE_ANALYSIS`
  at all, so a real Action invocation left it unset and every check in
  `run.sh` read it as `false` regardless of what the workflow requested
  (the existing tests missed this because they inject the env var
  directly rather than going through `action.yml`'s own wiring — closed
  by the pre-existing, now-passing `test_every_action_input_is_wired_to_
  run_sh` contract test, not a new one-off check); and a directory/package
  `compare` operand with the flag explicitly set used to silently drop it
  rather than reject the unsupported combination, running a release
  compare ungated despite the caller's explicit request — `run.sh` now
  fails the step loudly for that shape instead, the same treatment its
  own L2 compile-context and evidence-flag guards already give their own
  release-incompatible inputs.

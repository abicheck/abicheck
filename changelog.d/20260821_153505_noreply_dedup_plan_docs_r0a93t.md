### Documentation

- **Recorded a known, currently-unreachable gap in `evaluation_context_to_dict`'s
  write side**: a context decoded from a genuinely legacy
  (`schema_version == 1`) persisted payload, if a future caller attached
  real, non-default `gate.require_complete_analysis`/`gate.scope` values
  to it before re-serializing, would write a payload that mislabels
  itself (real v2 field values under a v1 stamp). Confirmed unreachable
  by any current production code path — nothing in the codebase
  constructs a non-default value for either field from real input yet.
  A correct fix is a write-side rejection of the mismatched combination
  (not a silent re-stamp, which would contradict this module's own
  "version fields survive verbatim" invariant), deferred to the
  dedup-and-convergence plan's Phase 2 resolver work, where the
  combination first becomes reachable (Codex review, PR #817, fifth
  round — see `docs/contribute/plans/duplication-and-convergence-assessment.md`'s
  Phase 2 item 1 section for the full reasoning).

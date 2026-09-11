<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **`check-target`'s `gate-mode: advisory` now neutralizes a no-baseline
  audit's own AUDIT_GATE axis.** A no-baseline audit's `run_outcome.gate`
  is always `PolicyGateDecision.NONE` regardless of AUDIT_GATE (it tracks
  the two-sided compatibility gate, not this candidate-side one), so the
  existing advisory-mode neutralization left `exit_axes.audit_gate`
  untouched, and the trailing `aggregate` job — which reads that field
  directly — still blocked CI on a real gating finding under an explicitly
  advisory check. `exit_axes.evidence_contract` is deliberately left
  unneutralized, matching how every other comparison-never-completed-style
  contribution in this envelope is carried over rather than zeroed.

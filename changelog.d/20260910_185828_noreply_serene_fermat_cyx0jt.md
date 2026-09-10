### Security

- **`compare --no-baseline`'s audit-gate axis now reads the policy-effective
  verdict, not a finding's raw `ChangeKind` category** — the axis added
  earlier the same day gated on `BREAKING_KINDS`/`API_BREAK_KINDS`
  membership computed straight from `change.kind`, so a `--policy`
  `overrides:`/`reclassify:` rule promoting a normally-`RISK`-classified
  finding to breaking was silently invisible to it: a CI job that had
  explicitly asked to gate could still exit `0` on an unsuppressed,
  policy-promoted finding. `policy/audit_gate_exit.py` now reads each
  finding's already-resolved `.verdict`
  (`policy.severity.effective_verdict_for_change`), the same value every
  other renderer honors. Found by an automated Codex security review on the
  introducing PR, fixed before merge.

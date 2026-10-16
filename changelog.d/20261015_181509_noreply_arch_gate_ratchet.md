<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **Architecture debt ratchet now binds** — `scripts/check_architecture.py` fails when a `no_growth` entry in `architecture/debt.yaml` sits above its file's real line count (`debt-slack`), so a file can no longer grow back to a stale ceiling unseen. `scripts/tighten_debt_baselines.py` lowers those baselines (only ever down, idempotent, formatting preserved). The ledger's slack went from 22,747 lines over 166 entries to zero; 35 entries whose files now sit under their size cap were removed, since the cap itself binds those files.
- **Typed scoped-gate result fields** — `--used-by` and `--required-symbol(s)` now stamp their result attributes through one typed `ScopedGateFields` value in `workflows/consumer_scope_gate.py` instead of 33 untyped assignments. The attribute names and values are unchanged.

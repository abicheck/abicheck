### Changed

- **ADR-061 adoption ledger**: `architecture/debt.yaml` baselines are tightened
  to each file's actual size, and `abicheck/severity.py` — which Phase 2's own
  work had already brought to 776 lines, below the 800-line production limit —
  no longer carries an entry at all, so it is permanently capped at 800. The
  ledger's `no_growth` rule caps a file at `max(baseline, PR base)`, so a file
  that shrank still carried a licence to grow back to its recorded baseline:
  473 lines of already-won ground across 15 files, unprotected. Phase 1's
  acceptance criterion already asked for this ("the relevant debt entries
  shrink or disappear"); it just had not been applied since.

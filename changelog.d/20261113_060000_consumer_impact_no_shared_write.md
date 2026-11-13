### Fixed

- **`--used-by` consumer proof paths no longer leak.** Enriching a consumer's
  covered findings wrote that consumer's proof path onto the library-wide
  `DiffResult.changes` objects, so it appeared in the unscoped report and the
  first consumer's path won for every later consumer (suppression rules read
  the same field). Each consumer now annotates its own copy; the copy stays
  the same disposition-ledger record, so the audit's detected total does not
  change.

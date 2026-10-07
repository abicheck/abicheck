### Documentation

- **ADR-049 records the evidence-adaptive default's fallback rule** — an
  in-place revision with a Revision history entry, stating what #1500
  implemented: header silence falls back to the observed export table,
  anything still unplaceable is scored as `--contract all` scores it, and ELF
  version-node findings are `NOT_APPLICABLE`. New regression tests pin the
  class: an exhaustive test over the fallback predicates, a producer-scan
  test for version-node kinds, and a real-CLI run of the 16 overlay and
  version-node catalog cases against their unchanged ground truth.

### Added

- **Subsumed findings name the finding that kept them** — a DWARF finding that the AST/DWARF collapse drops now records the retained finding it collapsed into (`related`: its kind, symbol and field) and the relation code (`relation: subsumed_by`) on its disposition record, alongside the existing `reason_code`. The retained finding is chosen the same way for every input order. The public report does not carry per-record entries yet, so this is visible through the in-memory ledger only.

### Added

- **`Change` gains an `evidence_provenance` field (G39 Phase 0/2).** A new,
  additive `tuple[str, ...] | None` field on `Change`, mirroring
  `contract_evidence_refs`' shape exactly — intended to eventually record
  which evidence tier(s)/provider(s) (e.g. `"l0:elf_symtab"`, `"l2:castxml"`)
  produced and corroborated a specific finding. `None` for every producer
  today (Phase 1's detector wiring has not started); a new completeness
  gate, `tests/test_evidence_provenance_completeness.py`, requires every
  `ChangeKind` to be classified in `tests/evidence_provenance_contract.py`
  so a kind's producer cannot be silently forgotten once wiring begins,
  mirroring the discipline `test_canonical_finding_id_completeness.py`
  already established for the same class of gap (PR #753 → #759). No
  detector behavior changes — the field is not yet read or set anywhere.
  See the G39 design plan (`docs/contribute/plans/g39-per-finding-evidence-provider-model.md`,
  added in PR #866 — merge that PR first if this file isn't present yet on
  the branch you're reading this from).

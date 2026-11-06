### Added

- **The clang header backend models C++20 concepts and template constraints**
  — a clang dump records each public concept's constraint and every public
  function/class template's type-constraints and requires-clauses
  (`AbiSnapshot.cxx_concept_constraints`, stored as the debug section's
  `abicheck.cxx_concept_constraints` extension fact; extractor generation 42).
  A new `concept_constraints` detector compares them with the L4 source
  lane's rule (now shared in `model/concept_constraint.py`): a tightened
  concept or a requires-clause added to a template is `concept_tightened`
  (API_BREAK), a relaxed or rewritten one `concept_constraint_changed`
  (RISK), and a renamed concept with the same body is no change. Catalog
  case105 now passes at debug-headers depth on clang, and a clang side that
  modelled its constraints no longer names the "C++20 concepts and
  requires-clauses" coverage gap. castxml cannot see concepts and keeps the
  `partial` note.

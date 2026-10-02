### Changed

- One evidence-merge rule, `abicheck.model.evidence_merge` (unknown ⊕ absent =
  unknown; only a completed read yields absent). A cross-TU merge where only
  some translation units captured `contract_attributes` now records the fact
  as `PARTIAL` instead of `PRESENT`; cheap BTF/CTF/DWARF section probes report
  a failed probe as `FAILED` instead of "no section"; the bundle export check
  reads pre-v46 visibility through `surface_facts.is_dynamically_exported`.

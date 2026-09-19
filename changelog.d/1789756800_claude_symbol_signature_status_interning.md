### Changed

- `SymbolSignatureStatus` values are now shared rather than allocated per
  symbol. The type is frozen, slotted, and carries two booleans, so it has
  exactly four inhabitants; `symbol_signature_statuses` built one object
  per symbol regardless. Measured on a real oneDAL release: 48 bytes
  against 87,728 symbols is 4.02 MiB for one library, and the release
  fan-out retains a mapping per matched member. Behaviour is unchanged --
  equality and hashing are identical, and no reader distinguishes two
  equal statuses by identity. A non-`bool` input is now normalized rather
  than stored verbatim, which also repairs the annotation the bare
  constructor could violate.

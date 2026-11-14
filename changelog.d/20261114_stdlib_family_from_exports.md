### Fixed

- **A library rebuilt against libc++ is reported as a standard-library
  change when headers are given** — the standard-library family was read
  from the declarations' manglings, which with headers come from the header
  parser (castxml reads libstdc++ headers whatever the library was built
  against), so a libstdc++ → libc++ rebuild compared with `-H` never reported
  `stdlib_implementation_changed`. The family is now read from the binary's
  own exported symbols (`model.build_mode_facts.stdlib_evidence_symbols`),
  both at dump time and at compare time, with declarations only as the
  fallback for a snapshot without a symbol table. Imports are not used:
  libc++ exports its exception classes in plain `std::`.

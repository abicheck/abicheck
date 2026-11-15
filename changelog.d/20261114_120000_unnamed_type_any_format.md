### Fixed

- `unnamed_type_in_public_abi` is no longer ELF-only: a newly exported symbol
  whose mangling carries an unnamed type or lambda closure is now flagged in
  Mach-O libraries (`__Z…Ut_`) and PE libraries (MSVC `<lambda_…>` /
  `<unnamed-type-…>`), read from every export table the snapshot carries.

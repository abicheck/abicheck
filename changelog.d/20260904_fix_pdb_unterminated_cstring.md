### Fixed

- **PDB CodeView name parsing now rejects an unterminated string as an
  incomplete record, instead of silently substituting an empty name** —
  `_read_cstring()` previously returned `("", len(data))` when no NUL
  terminator was found before the end of the buffer, indistinguishable from
  a legitimately empty, properly-terminated name (both could yield
  `new_offset == len(data)`). Every caller (`LF_STRUCTURE`/`LF_CLASS`/
  `LF_UNION`, `LF_ENUM`, and `LF_ARRAY` names, plus the `LF_MEMBER`/
  `LF_ENUMERATE`/`LF_ONEMETHOD` fieldlist sub-records and `LF_STMEMBER`/
  `LF_NESTTYPE`/`LF_METHOD`'s name-skipping in `_skip_subrecord`) treated
  this as a successfully parsed record, so a truncated PDB whose layout was
  silently dropped could still report the basic debug-evidence channel as
  `parsed` and let `--require-complete-analysis` pass. `_read_cstring()` now
  returns a third `terminated: bool` element, folded by every one of those
  call sites into the same `TypeDatabase.failed_record_count` completeness
  signal a prior fix introduced for non-exception truncation, so an
  unterminated name now correctly downgrades the channel to `partial`.

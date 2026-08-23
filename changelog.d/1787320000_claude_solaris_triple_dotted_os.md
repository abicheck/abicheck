### Fixed

- **The toolchain-triple validation added for a false-positive fix earlier
  in this PR was itself too strict for a real Solaris/AIX-style target
  triple.** `_TARGET_TRIPLE_RE` required every hyphen-joined component to
  be plain alphanumeric/underscore, but a real triple's OS component can
  embed a dotted version (`x86_64-pc-solaris2.11` — already recognized
  elsewhere in this codebase, in `toolchain_probe.py`). Under a
  relocatable prefix not covered by the fixed `_SYSTEM_HEADER_DIRS`
  prefixes, this made a genuine compiler's own private include tree read
  as ordinary project declarations, reintroducing noisy/false ABI findings
  from the toolchain surface it was supposed to be excluded from. Fixed by
  allowing a non-leading component to also carry dots.

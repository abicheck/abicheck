### Changed

- **One rank table for `compare`'s dominant exit axes** — the scalar pair
  ladder and the directory/package release ladder now both delegate the
  order of their dominant axes (`not_comparable` 16 > proven removed library
  8 > evidence contract 7 > budget overflow 5) to
  `policy/exit_rank.COMPARE_EXIT_RANK`. The scalar comparability refusal's
  exit 16 is decided by the same table instead of a CLI constant. No exit
  code, reason or `exit` block changed. Every one-member cell of the two
  ladders was enumerated: they agree except where a budget overflow decides
  the scalar exit, which the release ladder has no input for; that family is
  pinned by a test and left for a ruling under ADR-064.

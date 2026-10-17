### Fixed

- **One precedence owns a path reached by two routes** — the directory walk
  of the source-input pre-scan kept its own disposition ranking, separate from
  `SourceInputSet.merged`'s `DISPOSITION_PRECEDENCE`, and its comment promised
  the opposite of what its ranks did. Both now decide through
  `DISPOSITION_PRECEDENCE`. The one observable change: when a file is reported
  as both `missing` and `unreadable` for the same path, the label was decided
  by which route came first; it is now always `unreadable`, the stronger
  observation. Sufficiency is unchanged (both are gaps).

### Changed

- **The opaque-type by-value scan now short-circuits on a plain substring
  check before running its regex scans.** `_type_is_by_value_referenced`
  gates `_type_token_matches`/`_unqualified_type_token_matches` on `tname
  in text`/`leaf in text` first -- a token match always contains its own
  candidate as a substring, so this is behavior-preserving, and it skips
  the regex entirely for the common miss case on a snapshot with many
  opaque candidates and few actual references (CodeRabbit review on
  PR #1041).

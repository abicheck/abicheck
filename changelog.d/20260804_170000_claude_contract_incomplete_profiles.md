### Added

- `aggregate`'s profile matrix now carries `contract_incomplete_profiles`,
  naming the profiles whose ADR-049 contract-coverage axis is short of
  evidence. A profile whose only problem was contract coverage previously
  raised the aggregate exit to `1` while every list in the matrix stayed
  empty, so nothing identified the profile responsible. Kept separate from
  `affected_profiles` — that field is defined by verdict and gate, and §7
  requires an exit of `1` to remain attributable to a specific axis.

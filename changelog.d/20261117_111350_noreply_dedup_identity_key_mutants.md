### Changed

- **Cross-pass identity key no longer carries dead freezing code** — `finding_dedup.dedup_identity_key`'s inline validity check froze list and set inputs, but no dedup-table kind's identity reads such a field, so that branch was unreachable. The check now compares raw inputs; a change with a list or set field recomputes instead of hitting the cache. The `dedup_identity_key` mutants are all killed by new cross-pass tests, and the mutation selection now includes `tests/test_dedup_identity_memo.py`.

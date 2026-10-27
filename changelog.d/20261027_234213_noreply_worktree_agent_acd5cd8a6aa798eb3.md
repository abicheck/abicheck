### Fixed

- **A linked source-ABI surface re-encodes the keys its document states** — `SourceAbiSurface.from_dict` filled in `forced_public`, the mapping keys and the unmatched buckets a producer never wrote, so a load and re-save of an older `source/source_abi.json` added keys (and changed its digest). Absent keys now stay absent on decode; readers already used `.get(key, default)`, so no verdict changes.

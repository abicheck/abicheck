### Fixed

- **`OutputSpec` is now re-exported from `abicheck.service`** — it lived in
  `api_types.py` alongside `InputSpec`/`CompareRequest` (both already
  re-exported) but was missing from `service.__all__`, so
  `from abicheck.service import OutputSpec` failed even though the type is
  part of the same Tier-2 request/response family (ADR-037 D2).

### Documentation

- **Fixed a stale `schema_version` number in the Python API guide** —
  `docs/use/python-api.md` said snapshots carry `schema_version` `8`; the
  current value is `17`. The page now links to
  [Snapshot Format](../reference/snapshot-format.md), the fact owner for
  that number, instead of restating it.

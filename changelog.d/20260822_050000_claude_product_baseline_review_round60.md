### Fixed

- **`unpack_product_baseline()` now bounds the manifest member's own size
  before reading it, independent of the general decompressed-tar-wide
  ceiling.** That 8 GiB ceiling bounds total archive output, but does
  nothing to stop a single, highly-compressible manifest entry from
  allocating several times its own size across `read_text()` and
  `json.loads()`. The manifest is now checked against its own, much
  smaller limit (`DEFAULT_MAX_MANIFEST_BYTES`, 64 MiB, overridable via
  `_ABICHECK_PRODUCT_BASELINE_MAX_MANIFEST_BYTES`) before it is read at
  all.
- **`unpack_product_baseline()` now translates a `RecursionError` from
  excessively deep manifest JSON nesting into `SnapshotError`**, matching
  every other malformed-manifest case. `json.loads()` raises
  `RecursionError`, not `json.JSONDecodeError`, on sufficiently deep
  nesting, and the previous exception list didn't catch it -- letting a
  crafted manifest's raw `RecursionError` escape unhandled instead of the
  documented usage-error contract.

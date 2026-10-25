### Added

- **Coverage block counts exports the parsed headers did not declare when their signature names `sycl::`.** A `sycl::queue&` overload declared under `#ifdef SYCL_LANGUAGE_VERSION` is removed by the preprocessor when castxml parses without that macro; such exports are now reported per side in `coverage_warnings` (JSON and text) with the total of undeclared exports and the `sycl::` count. Informational only: no finding, no exit-code change. `docs/use/dump-compare-flags.md` shows how to parse the overloads with `-D SYCL_LANGUAGE_VERSION=...` and a stub header.

### Changed

- **The `sycl` detector's disabled reason now says it covers SYCL runtime (PI plugin) metadata**, not SYCL header APIs: `missing SYCL runtime (PI plugin) metadata; SYCL header APIs are covered by header parsing, not this detector`.

### Fixed

- SYCL header dumps with `icpx` 2026.1 failed with "clang AST output was not
  valid JSON: Extra data": that driver still runs the `spir64` device pass
  under `-fsycl -fsycl-host-only`. The host request now asks the driver
  (`-###`, memoized per executable) whether host-only really is one pass,
  and otherwise selects the host document from the multi-pass stream.

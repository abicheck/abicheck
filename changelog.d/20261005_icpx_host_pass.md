### Fixed

- SYCL header dumps with `icpx` 2026.1 failed with "clang AST output was not
  valid JSON: Extra data": that driver still runs the `spir64` device pass
  under `-fsycl -fsycl-host-only`. The driver is now asked (`-###`, memoized
  per executable) what it will run; a device + host pair is replayed directly
  (the device `-cc1` without its AST dump, for the integration header, then
  the host `-cc1` alone), so the request still yields one host AST document on
  the streaming path. Any other plan falls back to the multi-document
  selector. Measured on an 8-module SYCL library with system declarations
  kept: the header parse dropped from 83 s (multi-document selector, 2.4 GB of
  AST JSON) to 37 s, and the whole dump from 176 s to 114 s, with identical
  declarations.

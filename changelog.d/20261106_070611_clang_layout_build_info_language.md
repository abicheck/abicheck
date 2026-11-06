### Fixed

- **`--build-info` no longer drops record alignment under the clang header
  frontend** — the clang layout probe now parses headers in the same
  language as the main header pass. Before, it always used C++, so for a C
  library whose compile command carries a C `-std=` (for example
  `-std=gnu11`), clang rejected the probe and every record lost
  `alignment_bits`. A `dump --build-info`/`--sources` comparison then
  stopped reporting the `type_alignment_changed` that the same comparison
  without build info reported.

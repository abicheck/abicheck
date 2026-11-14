### Fixed

- **CPU-dispatch roll-up without a declared public-header set** — a removed
  `*_<isa>` specialisation parsed from a supplied header now grades the
  `cpu_dispatch_isa_dropped` roll-up BREAKING even when no public-header set
  was declared (Python `dump(headers=...)`, where every origin is
  `UNKNOWN`). Missing origin evidence no longer lets the ISA naming
  heuristic lower a header-declared removal to a risk; DWARF-only and
  out-of-public-set declarations still read export-only.
- **CastXML data size per target** — an empty class now has data size 0 on
  every Itanium target (it was its 1-byte `sizeof` when POD, which on Apple
  arm64's C++11 POD rule covers every empty class), and a record with a
  bit-field has no derived data size on MinGW/Cygwin, whose compilers
  allocate bit-fields Microsoft's way (`-mms-bitfields`).

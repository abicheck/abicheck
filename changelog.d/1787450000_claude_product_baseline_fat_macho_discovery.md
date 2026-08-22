### Fixed

- **`abicheck.product_baseline._macho_is_library_content`**: now recognizes
  a fat/universal Mach-O archive (the common shape for a distributed macOS
  framework binary, routinely shipping x86_64 + arm64 slices in one file)
  as a library, not just a thin single-architecture one. A universal
  framework binary previously had none of its fat-archive slices inspected
  — the content-aware fallback only ever read a thin `mach_header` — so a
  framework-only product could still silently compare as `NO_CHANGE` with
  no per-library comparisons run. Uses `macholib.MachO` (already a core
  dependency) to walk the fat archive's slices; recognized when *any*
  slice's filetype is a library type.

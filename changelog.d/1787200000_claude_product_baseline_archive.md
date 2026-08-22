### Added

- **`abicheck project baseline pack`/`unpack`** — a storage format for a
  whole multi-library *product* baseline, not just one library. Per-library
  `dump` snapshots don't scale to a product shipping several interdependent
  shared libraries (one release asset per library), and — more importantly
  — `scan --against <snapshot>` compares exactly one library against one
  snapshot, so a symbol one library imports from a sibling library
  disappearing is structurally invisible to it: no single per-library
  invocation ever sees both sides of that dependency edge.
  `abicheck.bundle` (ADR-023) is built for precisely this cross-DSO case,
  but needs real binaries on both sides. `project baseline pack SOURCE_DIR
  OUTPUT.tar.zst` archives an entire product directory (every shared
  library it ships, plus whatever else sits alongside them — debug info,
  installed headers) into one deterministic `.tar.zst` file, recording
  which archived files are libraries and which relative directories hold
  the product's public headers. `project baseline unpack ARCHIVE DEST_DIR`
  reverses it, reproducing a directory `compare`/`compare-release` can run
  against directly — checking every library, and every cross-library edge
  between them, in one bundle-aware invocation instead of one per-library
  `scan`. See `abicheck/product_baseline.py`'s module docstring.

### Added

- **`abicheck.product_baseline.pack_product_baseline`/`unpack_product_baseline`**
  — a storage format for a whole multi-library *product* baseline, not just
  one library. Per-library `dump` snapshots don't scale to a product
  shipping several interdependent shared libraries (one release asset per
  library), and — more importantly — `scan --against <snapshot>` compares
  exactly one library against one snapshot, so a symbol one library imports
  from a sibling library disappearing is structurally invisible to it: no
  single per-library invocation ever sees both sides of that dependency
  edge. `abicheck.bundle` (ADR-023) is built for precisely this cross-DSO
  case, but needs real binaries on both sides. `pack_product_baseline`
  archives an entire product directory (every shared library it ships, plus
  whatever else sits alongside them — debug info, installed headers) into
  one deterministic `.tar.zst` file, recording which archived files are
  libraries and which relative directories hold the product's public
  headers. `unpack_product_baseline` reverses it, reproducing a directory
  that directory-mode `compare` can consume directly. Library-only
  surface, no CLI command — see `abicheck/product_baseline.py`'s module
  docstring.
- **`abicheck.product_baseline.compare_product_directories`** — the
  plain-Python counterpart of directory-mode `compare <old_dir> <new_dir>`:
  given two directories (typically two `unpack_product_baseline()` results),
  discovers and matches every shared library both sides have in common, runs
  the per-library ABI compare on each matched pair, and correlates the
  results into a `BundleDiffResult` carrying both per-library changes and
  cross-library (bundle-level, ADR-023) findings — one call, no CLI
  subprocess, no directory-mode `compare` invocation. Previously the only
  way to get this combined result programmatically was to hand-assemble the
  same three steps yourself, or go through the CLI's private, Click-coupled
  `compare-release` engine. See `abicheck/product_baseline.py`'s module
  docstring.

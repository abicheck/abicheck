### Fixed

- **`abicheck.product_baseline.pack_product_baseline`**: OUTPUT placed
  inside a genuine, pre-existing declared header root that happened to be
  currently empty (e.g. `pack(..., "source/include/base.tar.zst",
  header_roots=["include"])`) had that directory unconditionally excluded
  from the archive as though it were scaffolding fabricated solely to
  hold OUTPUT -- the manifest still declared it as a header root, so an
  honestly produced archive rejected its own output on unpack. A declared
  header root is now excluded from that scaffolding check.
- **`abicheck.product_baseline._WINDOWS_RESERVED_NAMES`**: extended to
  cover `CONIN$`/`CONOUT$` (console I/O device names), `COM0`/`LPT0`, and
  the superscript-digit spellings `COM¹`/`COM²`/`COM³`/`LPT¹`/`LPT²`/`LPT³`
  -- legacy code-page renderings Windows still treats as equivalent to
  the plain-ASCII device names -- none of which the previous set caught.

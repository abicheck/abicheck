### Fixed

- **A header set with no declarations is no longer shown as high-confidence evidence** — the evidence-coverage table now reports the header (L2) layer of a side whose parsed header set declared nothing as `reduced confidence: header-scoped, zero public declarations` (per side in the by-side table), instead of `present, high confidence`. The finding and exit code are unchanged; only the stated confidence of the layer is lowered.
- **A development package given as `--header` to a single-pair `compare` is named, not dropped** — the run still reads only the two libraries (no headers from the package), but stderr now warns `development package '<path>' ignored for the <side>`, so a package passed to a comparison that cannot read it no longer disappears without a word. Use `compare` on the release directories to have the package's headers read.


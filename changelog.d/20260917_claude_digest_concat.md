### Changed

- **Content digests no longer copy the whole payload to prepend a domain
  prefix.** `storage/canonical.py`'s `_digest_from_payload` built
  `domain + payload` and hashed the result, materialising a complete second
  copy of the payload purely to put five bytes in front of it (44.2 MiB on a
  20,000-function snapshot's largest section). It now feeds the prefix and the
  payload to the digester as two `update()` calls. A digest is a function of
  the concatenated byte stream, not of how many calls delivered it, so every
  digest is bit-identical — verified against the previous implementation over
  3,000 randomised canonical values, and by the 2,858 existing
  storage/digest/package tests.

  This is waste removal, not a measured speed or peak improvement: the
  end-to-end digest peak is dominated by the sectioning layer upstream and does
  not move. See `docs/contribute/known-gaps.md` for that measurement, for the
  three adjacent reductions that were implemented and reverted for buying
  nothing, and for the measurement mistake that made one of them look like an
  18% win.

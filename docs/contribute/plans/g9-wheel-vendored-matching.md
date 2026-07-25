# G9 — manylinux/auditwheel vendored-library pairing

**Registry:** `UC-WF-wheel-vendored` (`planned`)
**Effort:** M · **Risk:** low

## Problem

`compare-release` on two manylinux/macOS wheels of the same project treats
**every vendored dependency as removed + re-added**, so the real
version-to-version ABI delta of the bundled stack is never computed.

`auditwheel` (Linux) and `delocate` (macOS) rewrite each vendored library to
`lib<name>-<hash>.so.<ver>` **and rewrite its `SONAME` to match**. The hash is
content-derived and changes on every rebuild, so library matching — which keys
on filename/SONAME — pairs nothing. Empirically (Pillow 10.4.0 → 12.2.0): 15
"library removed" + 18 "library added" warnings, and the real `libpng16`
16.43.0 → 16.56.0 / libjpeg / freetype / harfbuzz deltas are lost, even though a
direct file-to-file compare of the two `libpng16`s yields a clean `COMPATIBLE`
verdict — confirmed by empirically scanning real manylinux wheels.

**Corpus evidence.** A 43-wheel scan (popular C-extension packages, two releases
each) found this on **every** package that vendors a stack — 42 phantom
removed+added pairs in total:

| Wheel | Phantom-paired vendored libs | What is lost |
|---|---:|---|
| Pillow 9.5 → 11.0 | 15 | libjpeg 62.3→62.4, libpng16 16.39→16.44, freetype, harfbuzz, tiff, webp, lcms2, lzma 5.4→5.6, … (the whole stack) |
| opencv-python 4.8 → 4.10 | 14 | ffmpeg (avcodec/format/swscale), Qt5 5.15.0→5.15.13, openssl 1.1, libvpx 8→9 |
| psycopg2-binary 2.9.7 → 2.9.10 | 5 | libpq 5.15→5.16, openssl, openldap |
| pyzmq 25.1 → 26.2 | 2 | **libsodium SONAME 23→26**, libzmq |
| numpy / shapely / h5py | 2 each | libgfortran/libquadmath, libgeos, libhdf5 |

This is not only noise: it also causes **false negatives**. `pyzmq` bundles
`libsodium` with a `SONAME` bump `23.3.0 → 26.1.0` — a real ABI break of the
vendored dependency — which abicheck currently hides as "removed + added"
instead of flagging. Pairing by unhashed stem and then diffing per dependency
(below) surfaces it as the real signal.

## Goal & acceptance criteria

- [ ] `compare-release` pairs `lib<name>-<hashA>.so.<ver>` with
      `lib<name>-<hashB>.so.<ver>` across two wheels, computing a per-dependency
      verdict instead of removed/added noise.
- [ ] Pairing is driven by the **unhashed soname stem** (e.g. `libpng16.so.16`),
      derived by stripping the auditwheel/delocate suffix `-[0-9a-f]{6,16}` from
      both the filename and the ELF/Mach-O `SONAME`/install-name.
- [ ] A genuinely removed/added vendored lib is still reported as such.
- [ ] A paired vendored lib whose `SONAME` major bumps (e.g. the pyzmq
      `libsodium` `23 → 26` case) is reported as a real break, not absorbed by
      the normalization — this is the regression-test anchor.
- [ ] A two-wheel fixture proves the bundled stack is diffed.

## Design

1. A normalizer `strip_vendor_hash(name) -> stem` applied to both filename and
   soname before the existing `compare-release` matching pass.
2. Restrict the strip to the auditwheel/delocate shape (a `-<hex>` segment
   immediately before `.so`/`.dylib` or the version suffix) so ordinary
   hyphenated library names are untouched.
3. Match on the normalized stem; fall back to today's behaviour when no hash
   suffix is present.

## Files & surfaces

- `abicheck/cli_compare_release.py` (matching pass), a small helper in
  `abicheck/binary_utils.py` or `abicheck/package.py`.

## Tests

- Unit: `strip_vendor_hash` on auditwheel/delocate names and on names that must
  **not** be stripped (e.g. `libwebpdemux`, `libbrotlicommon`).
- Workflow: two synthetic wheels with hashed vendored libs → paired verdicts.

## Out of scope

Non-hash vendoring schemes; cross-platform bundle analysis (tracked under G1).

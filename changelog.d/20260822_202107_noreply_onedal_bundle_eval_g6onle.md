### Fixed

- **Directory/package `compare` now threads the L2 header compile context to
  its per-library fan-out.** `--ast-frontend`/`--compiler`/`--compiler-prefix`/
  `--compiler-option`/`--sysroot`/`--nostdinc`/`--frontend-context` (and the
  project `.abicheck.yml` `compile:` block) are resolved once for the whole
  release and applied to every library pair, the same way a single-pair
  `compare` applies them — instead of being rejected with a `UsageError`. Only
  a *sided* `--ast-frontend old=/new=` override still has no
  per-library-pair-within-a-release meaning and stays rejected.
- **Headerless (`-H`-less) directory/package `compare` now falls back to real
  ELF symbol visibility for public-surface scoping** instead of treating the
  surface as unresolvable and keeping every changed symbol as breaking. A
  default/protected-visibility exported symbol is treated as a public-surface
  proxy; a hidden/internal-visibility one is treated as private. Recorded as
  reduced-confidence scoping (`elf-visibility-fallback`) when it fires.
- **`--bundle-system-providers` now actually suppresses `bundle_intra_dep_removed`
  findings for a non-`std::`-shaped symbol** (e.g. a vendor C API symbol from
  MKL/TBB/SYCL) once every one of the consumer's external `DT_NEEDED` sonames
  is on the allow-list. A prior revision additionally required the *symbol
  name itself* to look system-shaped, making the flag inert for exactly the
  case it exists to cover. Soname matching also now falls back to a
  version-suffix-stripped comparison, so `--bundle-system-providers=libmkl_core`
  matches the real, versioned `libmkl_core.so.2` `DT_NEEDED` entry.

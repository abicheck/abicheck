### Fixed

- **The comparability gate now sees the target platform and an untagged
  dependency-scope baseline** — header dumps record the parse's effective
  target triple, pointer width and endianness (clang's flag-aware triple;
  for castxml, the emulated compiler's `__SIZEOF_POINTER__`/`__BYTE_ORDER__`
  under the forwarded flags), so `-m32`/`--target=` on only one side of a
  same-architecture comparison is refused as a `ProfileMismatchError`
  instead of reported as ABI findings. A baseline that never recorded these
  fields stays comparable (unrecorded is unknown, never a mismatch). A pair
  where only one side carries `dependency_scope` (a pre-v18 baseline) is no
  longer a clean pass: it is compared with `declaration`/`layout` marked
  unverified and the reason in `coverage_warnings`.
  The host-platform guess the clang backend uses when no compiler probe
  answers is never recorded as the effective target; `-mx32` keeps the
  `x86_64` architecture (`x86_64-…-gnux32`), not `i686`; and the memoized
  target probe re-runs when an `@response-file`/`--config=` file's contents
  change in place.

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

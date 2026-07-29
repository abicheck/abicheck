### Fixed

- **A declared `compiler_family` that conflicts with a binding actually
  resolving to MSVC is no longer silently exempted.** The resolved-path
  MSVC skip added in the previous fix applied unconditionally, so a
  profile explicitly declaring `compiler_family: gcc` (or any other
  non-MSVC family) whose binding resolved to a real `cl.exe` was also
  silently passed — the exemption is now gated on `declared_family` being
  empty, so an explicitly conflicting declared family still reaches the
  probe and is reported.
- **Two target triples that are both entirely unrecognized by the OS/
  environment marker tables no longer silently pass just because their
  architecture agrees.** `arm-none-eabi` (bare-metal EABI) vs.
  `arm-none-elf` (bare-metal ELF) share an architecture and neither
  normalizes to a known OS or environment marker, so the existing
  OS/environment mismatch checks stayed silent — a real ABI/object-format
  difference passed unconditionally. Added a fallback: when neither side's
  OS nor environment marker is recognized, the raw, unrecognized target
  suffix (everything after the architecture) is compared verbatim, and a
  difference there is now reported as an error too.

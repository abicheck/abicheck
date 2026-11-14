### Fixed

- **CPU-dispatch roll-up, overload keys, castxml data size and type
  references read their evidence exactly** — a removed ISA member counts as
  header-declared only when its origin is the library's own public header
  (a DWARF-only function or a header-only dependency's internals no longer
  grade the roll-up `BREAKING`), and a raw MSVC export matches a header
  declaration by its qualified name, never a bare identifier. Export-only
  overload keys keep each parameter's namespace (`put(a::Token)` vs
  `put(b::Token)`), and the dependency-internals stems use the detector's
  own normalization. castxml's derived `data_size_bits` is claimed only for
  an Itanium C++ ABI target, allocates a base by its non-virtual size, keeps
  every `#if` arm of a `[[no_unique_address]]` macro, and memoizes its
  hierarchy queries. `mangling_references_type` reads ABI tags with their
  component and MSVC class codes only at type boundaries. The unnamed-type
  detector is disabled when the baseline's export table was not read in
  full; typedef origins weigh both typedef maps, a qualified-only public
  typedef seeds the surface, and the fact is written even when the debug
  section is unreadable.
- **castxml data size follows the target's tail-padding rule** — the
  derivation now reads the target triple castxml parsed for and applies
  the generic Itanium C++03-POD rule (Linux, MinGW, x86 Darwin) or Apple
  arm64's C++11-POD rule (a class whose members are all private is POD
  there); an MSVC target, an unmodeled one or an unrecorded triple claims
  no data size. A header function whose mangling changed but which is
  still declared no longer grades the CPU-dispatch roll-up `BREAKING`
  (its own `func_params_changed` reports it).

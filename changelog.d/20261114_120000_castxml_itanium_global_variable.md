### Fixed

- castxml: an `extern "C"` global variable declared in a C++ header is keyed by
  its bare symbol again. castxml writes a `_Z<len><name>` guess for it, but the
  Itanium ABI never mangles a global-namespace variable, so the C-header gate on
  the extern-C recovery no longer keeps that guess (MSVC decorations still stay
  behind the gate).

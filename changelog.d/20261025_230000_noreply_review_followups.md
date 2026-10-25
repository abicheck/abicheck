### Fixed

- **A header-declared function at a bare Windows path is now paired by its
  real header name.** The header-path parameter pairing (#1812) keyed a
  declaration on the text before the last `:`, so a location such as
  `C:\inc\api.h` with no line number read as header `C`. Only a trailing
  `:line` is stripped now.

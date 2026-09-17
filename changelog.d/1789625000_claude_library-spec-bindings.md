### Added

- **`actions/baseline`'s declarative component spec can bind build-decided
  values.** A declaration checked into a repository cannot spell a
  dependency's install prefix or a host-arch directory component sitting in
  the middle of `lib/${HOST_ARCH}/libfoo.so*`, so every project that needs
  one wrote its own substitution pass beside its own capture step — and a
  textual one mangles any value containing `&`, a quote, a backslash or the
  delimiter, none of which is rare in a filesystem path. The new
  `library-spec-bindings` input (`resolve-libraries --bind NAME=VALUE`,
  `resolve_library_set(..., bindings=...)`) fills `${NAME}` placeholders by
  walking the document as JSON and inserting each value literally. The given
  set is the whole allowlist: an unbound `${...}` is refused rather than left
  as text or read from the environment, and a bound value is never
  re-scanned, so this is a binding rather than a templating language.

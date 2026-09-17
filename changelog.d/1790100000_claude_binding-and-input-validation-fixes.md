### Fixed

- `library_selection.bind_declaration` now refuses a binding that collapses
  two distinct keys of the same object onto one key, instead of silently
  keeping whichever was written last.
- `resolve_library_set` treats an explicitly empty `bindings={}` as "this
  declaration binds nothing", so an unbound `${NAME}` is still refused; it
  previously skipped binding entirely on any falsy map and let the
  placeholder through to be parsed as a literal path.
- `actions/verify-source-run` validates `require-provenance` as exactly
  `true` or `false` — a misspelling silently disabled the requirement —
  rejects a `provenance-from`/`report-from` member name that escapes the
  extraction destination, and reports `tested-sha-source: input` for a
  caller-stated commit rather than mislabelling it as the run's head.
- Arrays that can be empty are no longer expanded bare in shell steps:
  under macOS's bash 3.2 and `set -u` that is an unbound-variable error,
  not an empty expansion.
- `actions/verify-source-run`'s input refusals now emit a machine-readable
  `refusal-code` (`require-provenance-invalid`, `member-name-unsafe`,
  `member-name-mismatch`) instead of failing with an empty one, which a
  consumer could not distinguish from any other failure.

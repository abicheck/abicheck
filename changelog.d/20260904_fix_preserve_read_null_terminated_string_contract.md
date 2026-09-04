### Fixed

- **`type_metadata.read_null_terminated_string()` keeps its original
  `-> str` return contract** — an earlier fix in this same area widened it
  to `(str, bool)` to signal an invalid offset/missing terminator, which
  would have silently broken any existing caller using the result as a
  plain string (including a bare truthiness check, since a two-element
  tuple is always truthy). The validity signal is now an opt-in
  `invalid: list[bool] | None` out-param, matching this codebase's other
  opt-in completeness out-params (`truncated`, `decode_failed`); the two
  real callers (BTF's and CTF's own module-private `_read_string`
  wrappers) still expose the `(name, valid)` tuple shape their own
  callers need, computed locally from the out-param.

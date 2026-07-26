### Documentation

- **Documented a known, accepted limitation of the `include_sequence`
  owned-header-growth carve-out: a new dependency pulled in solely by an
  appended header, reachable only through a non-owned `ext:`/`sys:`
  bucket, still hard-fails the comparability gate with
  `ProfileMismatchError`.** Unlike the owned `hdrs:` slot (an explicit
  JSON list of `(identity, relative_path)` pairs, so superset growth can
  be verified), an `ext:`/`sys:` slot's token is a single opaque
  `_sha256_of` digest over its entire file set — there is no per-file
  identity recoverable from two hash strings to tell "this dependency is
  new" apart from "this external directory's contents genuinely drifted."
  This is the conservative, safe failure mode (a real hard-fail, never a
  silently wrong verdict); closing it would mean changing what an
  `ext:`/`sys:` slot stores (a `profile_fingerprint` wire-format change),
  not a carve-out logic tweak, so it's recorded in ADR-050 alongside the
  earlier common-root-rebasing limitation rather than attempted as a
  drive-by fix. `--diagnostic-comparison` remains the correct workaround.

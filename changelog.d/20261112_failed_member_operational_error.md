### Fixed

- A release member that failed to extract or compare (ADR-065 D8) is now an
  operational error (exit `4`) under the default `warn` policy. Previously a
  failed member beside a completed one exited `0` with reason `clean`, even
  though the scope record marked it `failed`. This covers a matched degraded
  member and a degraded member present only on OLD, which has no library
  result. A failed member next to a breaking one stays `4`, and a release
  where every member was compared is unchanged.

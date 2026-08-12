### Fixed

- **`publish-baseline.yml`'s safe-retry comparison now recomputes an
  existing release asset's content digest from the actual extracted
  snapshot/binary bytes**, not from the manifest's own declared
  `sha256`/`binary_sha256` fields — a hand-edited or otherwise-corrupted
  existing asset whose manifest declared a digest matching a fresh run's
  real digest could previously pass as a safe retry even though its actual
  content differed.
- **The existing-asset profile-identity check now requires the recorded
  profile to be nonempty and equal**, not merely "not provably different"
  — an existing manifest with no `profile` recorded (a valid possibility,
  since `actions/baseline`'s `profile` input is optional) previously
  skipped the check entirely.

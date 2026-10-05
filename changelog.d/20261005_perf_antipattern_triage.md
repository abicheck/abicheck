### Changed

- Whole-token spelling matching no longer re-probes, character by character,
  every registered spelling that is a prefix of a longer token; its cost is
  now independent of how much the vocabulary's names overlap.
- Every loop anti-pattern the `perf-antipatterns` gate had baselined was
  triaged: the wasteful ones are fixed, the inherent ones carry an in-place
  `# perf-ok: <reason>` exemption, and the baseline is empty.

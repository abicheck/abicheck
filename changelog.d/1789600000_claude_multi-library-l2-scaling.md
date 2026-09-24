### Performance

- **A directory/package `compare` at header depth no longer repeats the two
  most expensive per-member header computations.** Every member of a release
  fan-out is dumped against the release's whole header and include set, so
  per-member work that walks that set grows with the member count, and the
  release total grew roughly with its square. Two such walks dominated. First,
  extraction-contract fingerprinting resolved paths (`Path.resolve()`) afresh
  on every dependency × header/include-root test. It now resolves each path
  once per contract computation and answers ancestry from a precomputed
  ancestor set. Second, the C++20 dialect scan ran several times per dump over
  the identical set. It is now memoized in-process, validated by content
  digests of every file it reached and listings of their directories, so an
  edit, a newly-resolving quoted include, or a deletion is always rescanned.
  On a generated 2-headers-per-library fixture (4 CPUs, cold cache):
  10 libraries 11.5 s → 8.6 s, 16 libraries 32.2 s → 19.1 s. The marginal
  cost exponent over 1–10 libraries fell from 1.64 to 1.39. Output is
  unchanged. Some super-linear cost remains, because each member still
  receives the union header set.
- The quoted-include expansion used by the dialect scan moved to
  `abicheck/extract/quoted_include_expansion.py`, and
  `signature_normalization`'s nesting-aware scanners moved to
  `abicheck/model/nesting_scan.py`. The second move brings that module back
  under the ADR-061 800-line ceiling it crossed in #1362.

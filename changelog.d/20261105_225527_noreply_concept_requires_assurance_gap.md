### Fixed

- **C++20 concepts and requires-clauses are named as unobserved** — neither
  header backend models a `concept` or a `requires` constraint, so a
  tightened concept compared from headers read `NO_CHANGE` with `complete`
  assurance on clang. A castxml or clang dump now scans its public headers
  for a concept definition or a requires-clause (comments, string literals
  and `#define` lines are ignored) and stores the result as the optional
  `abicheck.cxx_concept_header_scan` extension fact of the `debug` section
  (no snapshot schema bump; extractor generation 41). When either side
  declares one, the run reads `partial` and the coverage gaps name
  "C++20 concepts and requires-clauses (not modelled by header backends)".
  The verdict is unchanged: the constraint change itself is still not
  detected.

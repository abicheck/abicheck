<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **C++20 auto-detection now recognizes `consteval`, `constinit`, and
  bare abbreviated function template parameters.** A header whose only
  C++20 signal was `consteval int f();`, `constinit extern int x;`, or an
  unconstrained abbreviated template (`void f(auto x);`) was previously
  parsed under the pre-C++20 default dialect, rejecting an otherwise-valid
  header. The abbreviated-parameter check is careful to exclude a generic
  lambda's `auto` parameter (`[](auto x) { ... }`), which has been valid
  since C++14 and must not be mistaken for the C++20-only form.
</content>

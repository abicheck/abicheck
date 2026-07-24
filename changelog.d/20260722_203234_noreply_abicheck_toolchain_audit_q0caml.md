<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **The abbreviated function template detector now sees a standard
  attribute before the parameter's bare `auto`.** A header whose only
  C++20 signal was `void f([[maybe_unused]] auto x);` left the scanned
  prefix ending in the attribute's closing `]]` instead of the enclosing
  `(`/`,`, so `_detect_cpp20_headers()` returned false and the header was
  parsed under the pre-C++20 default dialect. `[[attr]]`/`[[attr(args)]]`
  attribute-specifier-seq entries are now stripped before the `(`/`,`
  check, same as the existing cv-qualifier strip.
</content>

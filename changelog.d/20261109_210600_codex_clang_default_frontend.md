### Changed

- Unpinned `auto` header analysis now selects Clang. Install clang/clang++ or pin `compile.frontend: castxml` to retain CastXML. Explicit frontend requests and environment pins retain precedence; failures do not silently switch producer.

### Fixed

- Recognize C++ `extern "C"` declarations after another declaration on the same header line, while preserving explicit C language requests.

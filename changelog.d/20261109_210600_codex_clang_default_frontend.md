### Fixed

- Direct Clang header extraction records compiler-confirmed ordinary and packed enum representations, including `-fshort-enums`, instead of assuming `int`.
- Clang function parameters and returns carry scoped record/enum identities through aliases and namespace imports; ambiguous lookup stays unconfirmed.
- Recognize C++ `extern "C"` declarations after another declaration on the same header line, while preserving explicit C language requests.

### Documentation

- Explain header frontend selection for GCC, Clang versions and SYCL. The unpinned default remains CastXML; switching every scan to Clang is deferred pending semantic coverage parity.

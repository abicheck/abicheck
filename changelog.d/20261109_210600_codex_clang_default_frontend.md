### Fixed

- Direct Clang header extraction records compiler-confirmed ordinary and packed enum representations, including `-fshort-enums`, instead of assuming `int`.
- Clang function parameters and returns carry scoped record/enum identities through aliases and namespace imports; ambiguous lookup stays unconfirmed.
- Recognize C++ `extern "C"` declarations after another declaration on the same header line, while preserving explicit C language requests.
- The built-in Clang record-layout pass follows the C/C++ mode the primary header parse resolved instead of re-guessing C from a `.h` suffix. Without DWARF (`-g0`, or any Mach-O dylib) a public header that includes C++ declarations had no record sizes, so a reached record's growth compared clean (exit 0 instead of 4).
- A malformed Clang enum-probe AST (an `inner` that is not a list, a non-object child) declines the whole probe; the representation stays unknown (`?`), never a partial answer or an uncaught error.
- Extractor generation 52 and snapshot cache v46: stored or cached Clang dumps from before this fix keep the assumed `int` and lack slot identities; re-dump them.

### Documentation

- Explain header frontend selection for GCC, Clang versions and SYCL. The unpinned default remains CastXML; switching every scan to Clang is deferred pending semantic coverage parity.

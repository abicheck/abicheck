<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **A libstdc++ dual-ABI flip of one or two functions is detected.** The `glibcxx_dual_abi` detector required at least five removed and five added exports, so flipping `_GLIBCXX_USE_CXX11_ABI` on a library that exported only a few `std::string` functions reported nothing. The detector now pairs each removed and added export whose demangled identity agrees once the dual-ABI spellings are removed and whose `std::__cxx11` spelling differs (a bare `[abi:cxx11]` tag change is not a flip); the raw-marker mass heuristic remains only for symbols the demangler cannot read.
- **ABI-tag findings are no longer hidden by a raw-marker threshold.** `abi_tag` suppressed every `ABI_TAG_CHANGED` finding whenever the marker count crossed the mass threshold. It now suppresses only a tag change that is exactly one reported dual-ABI flip pair; every other tag change is reported. A namespace move such as `api::__cxx11::f` to `api::f` is no longer mistaken for a flip.

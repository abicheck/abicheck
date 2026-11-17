<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **A libstdc++ dual-ABI flip of one or two functions is detected.** The `glibcxx_dual_abi` detector required at least five removed and five added exports, so flipping `_GLIBCXX_USE_CXX11_ABI` on a library that exported only a few `std::string` functions reported nothing. The detector now pairs each removed and added export whose demangled identity agrees once the dual-ABI spellings are removed and whose `__cxx11` tag differs; the raw-marker mass heuristic remains only for symbols the demangler cannot read.

# Identical C++ source for v1 and v2; the only difference is the libstdc++
# dual-ABI selection macro. v1 builds with the legacy std::string ABI
# (_GLIBCXX_USE_CXX11_ABI=0), v2 with the C++11 ABI (=1). This rewrites the
# mangled names of every std::string-bearing symbol.
#
# No V1_HEADERS/V2_HEADERS are declared: the dual-ABI flip is detected from the
# binaries' symbol tables, so the case is validated binary-only and does not
# depend on parsing <string> through castxml. GNU libstdc++ only — macOS clang
# uses libc++, which has no dual-ABI tag.
abicheck_add_case(case104_glibcxx_dual_abi_flip
    LANG CXX
    V1_SOURCES v1.cpp
    V2_SOURCES v2.cpp
    V1_COMPILE_OPTIONS -D_GLIBCXX_USE_CXX11_ABI=0
    V2_COMPILE_OPTIONS -D_GLIBCXX_USE_CXX11_ABI=1
    APP_SOURCES app.cpp
    PLATFORMS linux
)

abicheck_add_case(case180_symbol_binding_lost_unique
    LANG CXX
    V1_SOURCES v1.cpp
    V2_SOURCES v2.cpp
    V1_HEADERS v1.hpp
    V2_HEADERS v2.hpp
    # -fno-gnu-unique is GCC-only (unrecognized by clang, which never emits
    # STB_GNU_UNIQUE in the first place -- see the known_gap_toolchains entry
    # in ground_truth.json).
    V2_COMPILE_OPTIONS $<$<CXX_COMPILER_ID:GNU>:-fno-gnu-unique>
    APP_SOURCES app.cpp
    PLATFORMS linux
)

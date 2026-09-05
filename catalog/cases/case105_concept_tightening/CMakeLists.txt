abicheck_add_case(case105_concept_tightening
    V1_SOURCES v1.cpp
    V2_SOURCES v2.cpp
    V1_HEADERS v1.h
    V2_HEADERS v2.h
    APP_SOURCES app.cpp
    # Concepts require C++20 — override the examples-wide C++17 default.
    # Generator expressions keep the flags compiler-correct: MSVC uses
    # /std:c++20, gcc/clang use -std=c++20.
    V1_COMPILE_OPTIONS "$<IF:$<CXX_COMPILER_ID:MSVC>,/std:c++20,-std=c++20>"
    V2_COMPILE_OPTIONS "$<IF:$<CXX_COMPILER_ID:MSVC>,/std:c++20,-std=c++20>"
    APP_COMPILE_OPTIONS "$<IF:$<CXX_COMPILER_ID:MSVC>,/std:c++20,-std=c++20>"
    PLATFORMS linux macos
)

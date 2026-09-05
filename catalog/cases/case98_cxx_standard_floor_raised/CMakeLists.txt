abicheck_add_case(case98_cxx_standard_floor_raised
    V1_SOURCES v1.cpp
    V2_SOURCES v2.cpp
    V1_HEADERS v1.h
    V2_HEADERS v2.h
    V2_COMPILE_OPTIONS -std=c++20
    APP_SOURCES app.cpp
    PLATFORMS linux macos windows
)

abicheck_add_case(case180_symbol_binding_lost_unique
    LANG CXX
    V1_SOURCES v1.cpp
    V2_SOURCES v2.cpp
    V1_HEADERS v1.hpp
    V2_HEADERS v2.hpp
    V2_COMPILE_OPTIONS -fno-gnu-unique
    APP_SOURCES app.cpp
    PLATFORMS linux
)
